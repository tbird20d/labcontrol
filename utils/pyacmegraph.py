#!/usr/bin/env python3
""" ACME power probe capture and analysis tool
"""

import numpy as np
import iio
import sys
import argparse
import struct
import threading
import time
import os
import copy
import pickle
import xmlrpc.client
import types
import re

__license__ = "MIT"
__status__ = "Development"

# ACME settings
integration_time = "0.000588"
in_oversampling_ratio = "1"
max_freq = 800  # experimental max sampling freq limit because if I2C link (in Hz)

parser = argparse.ArgumentParser(description='ACME measurements capture and display tool.',
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog='''
This tools captures exclusively Vbat and Vshunt values from ACME probes. Using Rshunt
(auto-detected or forced), it computes and displays the resulting power (Vbat*Vshunt/Rshunt).
Capture settings are automatically setup to optimize sampling resolution, but can be overriden.
Example usage:
''' + sys.argv[0] + ''' --ip baylibre-acme.local --shunts=100,50,250 -v
''')
parser.add_argument('--load', metavar='file',
                    help='''load .acme file containing data to display (switches
                        to display-only mode)''')
parser.add_argument('--template', metavar='file',
                    help='''load .acme file settings section only (colors,
                        plot names, shunts ...). Useful for starting a fresh
                        capture session re-using a previous saved setup''')
parser.add_argument('--inttime', metavar='value', nargs='?', default='',
                    help='integration time to use instead of default value ('
                    + integration_time + 's). Use without value to get the list '
                    'of accepted values')
parser.add_argument('--oversmplrt', metavar='value', type=int,
                    help='oversampling ratio to use instead of default value ('
                    + in_oversampling_ratio + ')')
parser.add_argument('--norelatime', action='store_true',
                    help='display absolute time from device')
parser.add_argument('--ip', help='IP address of ACME')
parser.add_argument('--shunts',
                    help='''list of shunts to use in mOhms (comma separated list,
                        one shunt value per channel, starting at channel 0) Ex: 100,50,250''')
parser.add_argument('--meancapture', action='store_true',
                    help='''Capture mean values only (compute the mean values of the capture
                            buffers and store only these mean values). Full data are dropped.
                            Helps with long captures.''')
parser.add_argument('--vbat', type=float, help=''' Force a constant Vbat value (in Volts)
                    to be used for computing power, in place of ACME measured vbat''')
parser.add_argument('--ishunt', action='store_true',
                    help='Display Ishunt instead of Power')
parser.add_argument('--forcevshuntscale', metavar='scale', nargs='?', default=0, type=float,
                    help='''Override Vshunt scale value, and force application start even
                    if identifying a Vshunt scaling problem''')
parser.add_argument('--timeoffset', metavar='time', type=float, help='''Add an offset to displayed time
                    (can be negative) in offline mode''')
parser.add_argument('--verbose', '-v', action='count', default=0,
                    help='print debug traces (various levels v, vv, vvv)')
parser.add_argument('--filename', metavar='value', type=str, help='''Name of csv file to store power readings''')
parser.add_argument('--samplingtime', metavar='value', type=int, default=1, help='''Time interval between successive power measusurement (in seconds)''')
parser.add_argument('--probe', metavar='value', type=int , help='''Probe number''')


args = parser.parse_args()
if args.verbose >= 3:
    print("args: ", args)

dir = os.path.dirname(__file__)

if args.oversmplrt and args.oversmplrt > 0:
    in_oversampling_ratio = str(args.oversmplrt)

# channels mapping: 'name used here' vs 'ACME naming'
cdict = {   'Vshunt' : 'voltage0',
            'Vbat' : 'voltage1',
            'Time' : 'timestamp',
            'Ishunt' : 'current3',
            'Power' : 'power2',
            }

if args.filename:
    filename = args.filename
    with open(filename, "w+") as f:
        f.write("%s,%s,%s\n" %("timestamp", "voltage", "current"))

probe = None
if args.probe:
    probe = args.probe

if args.samplingtime:
    samplingtime = args.samplingtime

# channels to enable (will be sent from ACME over I2C and up to app buffers)
enadict = { 'Vshunt' : True,
            'Vbat' : True,
            'Time' : True,
            }

# table containing all data for all channels
databufs = []
plotindex = 0
# dict containing all additional variables related to display settings
dispvars = {}

# state variable for initializing parameters from external file (template feature)
tmpl_setup = False

# Display power by default, but can display Ishunt alternatively (must be selected before init)
dispvars['display Ishunt'] = False

# default strings for displaying captured data (default Power, but can be changed to Ishunt)
dispstr = {}
dispstr['pwr_ishunt_str'] = "Power (mW)"
dispstr['pwr_plot_str'] = "Power plot"
dispstr['pwr_color_str'] = "Power color"

# Handle XMLRPC services related to an ACME device
class acmeXmlrpc():

    def __init__(self, address, probe=None):
        self.setup = False
        self.dev2phy = {}
        serveraddr = "%s:%d" % (address, 8000)
        self.proxy = xmlrpc.client.ServerProxy("http://%s/acme" % serveraddr)
        self.probe = probe
        print (self.probe)
        # probe for each ACME Probe and generate a table linking physical Probe sockets
        # with IIO devices
        dev_index = 0
        if self.probe:
            try:
                info = self.proxy.info("%s" % self.probe)
            except:
                if args.verbose >= 1:
                    print("  No XMLRPC service found for this device")
                return
            if str(info).find('Failed') != -1:
                # Probe socket no used
                if args.verbose >= 2:
                    print(("  XMLRPC: Probe socket %d empty" % (i)))
            else:
                if args.verbose >= 1:
                    print(("  XMLRPC: Probe socket %d CONNECTED (IIO:%d)" % (self.probe, dev_index)))
                self.dev2phy[dev_index] = 1
        else:    
            # support up to 16 probes
            for i in range(1,17):
                try:
                    info = self.proxy.info("%s" % i)
                except:
                    if args.verbose >= 1:
                        print("  No XMLRPC service found for this device")
                    return
                if str(info).find('Failed') != -1:
                    # Probe socket no used
                    if args.verbose >= 2:
                        print(("  XMLRPC: Probe socket %d empty" % (i)))
                else:
                    if args.verbose >= 1:
                        print(("  XMLRPC: Probe socket %d CONNECTED (IIO:%d)" % (i, dev_index)))
                    self.dev2phy[dev_index] = i
                    dev_index +=1
        self.setup = True

    # The info service provides informations not exposed through IIO
    def info(self, index):
        infod = {}
        if not self.setup or index not in self.dev2phy:
            return infod

        try:
            info = self.proxy.info("%s" % self.dev2phy[index])
        except:
            if args.verbose >= 1:
                print("XMLRPC error")
            return infod
        if str(info).find('Has Power Switch') != -1:
            infod['power switch'] = True
        match = re.match(r'PowerProbe (.+) \(', str(info))
        if match:
            infod['name'] = match.group(1)
        match = re.search(r'Serial Number: (\S+)', str(info))
        if match:
            infod['serial'] = match.group(1)
        return infod

# Handle a device (setup channels), retrieve and format data and store them into data buffer
# Then the main thread can read from data to plot it.
# The global data_thread_lock lock shall be used when accessing data.
class deviceThread(threading.Thread):

    def __init__(self, threadid, dev, rshunt, ndevices, enadict, vbat=0, ishunt=False, xmlrpc=None):
        print (dev)
        threading.Thread.__init__(self)
        self.crdict = {}
        self.scaledict = {}
        self.abs_start_time = 0
        self.first_run = True
        self.running = True
        self.sample_period_stats_mean = 0
        self.estimated_freq = 0
        self.shunt_override = False
        self.buf = None
        self.power_switch = False
        self.meta = {}
        self.dev = dev
        self.ndevices = ndevices
        self.data = np.empty((0, 3))
        self.meandata = np.empty((0, 3))
        self.sample_period_stats = np.empty(0)
        self.enadict = enadict
        self.vbat = vbat
        self.ishunt = ishunt
        self.capture_index = 0
        print("Configuring new device %d of %d. Name: %s ; id: %s" %(threadid + 1, ndevices, dev.name, dev.id))
        # set oversampling for max perfs (4 otherwise)
        dev.attrs['in_oversampling_ratio'].value = in_oversampling_ratio
        # enforce synchronous reads
        dev.attrs['in_allow_async_readout'].value = "0"
        if args.verbose >= 1:
            print("Showing attributes for %s" % (dev.id))
            for k, at in list(dev.attrs.items()):
                print("   %s (%s)" % (at.name, at.value))
        # configuring channels for this device
        for k, v in list(cdict.items()):
            ch = dev.find_channel(v)
            if ch:
                if args.verbose >= 1:
                    print("Found %s channel: %s (%s)" % (k, ch.id, ch.attrs['index'].value))
                if self.enadict.get(k):
                    if ch.attrs.get('scale'):
                        scale = float(ch.attrs.get('scale').value)
                        if k == "Time":
                            print("WARNING: scale on Time channel!!!")
                        # Check Vshunt scale
                        if k == "Vshunt" and scale != 0.0025:
                            print(("Error: suspicious scale value on Vshunt channel" \
                                " (found %f instead of 0.0025 expected)!" % (scale)))
                            print("Measurements may be wrong! Check ACME file-system version." \
                                    " (use --forcevshuntscale option to force app start)")
                            if args.forcevshuntscale == 0:
                                # argument not provided
                                sys.exit(0)
                        if k == "Vshunt" and args.forcevshuntscale != 0:
                            if args.forcevshuntscale == None:
                                print(("Using default Vshunt scale value (%f)" % (scale)))
                            else:
                                scale = args.forcevshuntscale
                                print(("Forcing Vshunt scale to %f"% (scale)))
                    else:
                        scale = 1.0
                    self.scaledict[k] = scale
                    if args.verbose >= 1:
                        print("   scale: %f" % (scale))
                    if ch.attrs.get("integration_time"):
                        # change integration time for max capture rate
                        ch.attrs.get("integration_time").value = integration_time
                    if args.verbose >= 1:
                        print("   enabling...")
                    ch.enabled = True
                # print ch.scan_element
                # print ch.attrs
                self.crdict[k] = ch
            else:
                print("Could not find %s channel..." % (k))
                sys.exit()
        self.sampling_freq_acme = float(dev.attrs['in_sampling_frequency'].value)
        # clip to the maximum sampling freq achieve-able with the BBB i2c bus
        # anyway, keep track of the acme setup for reference
        self.sampling_freq = int(min(max_freq, self.sampling_freq_acme) / self.ndevices)
        if args.verbose >= 1:
            print("Configured sampling frequency: %.0fHz (acme: %f)" % (self.sampling_freq, self.sampling_freq_acme))
        # Adjust buffer size based on expected frequency
        # size buffer to store 0.5s if possible
        buffer_size = int(self.sampling_freq / 2)
        if buffer_size < 64:
            buffer_size = 64
        if args.verbose >= 1:
            print("Adjusted buffer size to %d samples" % (buffer_size))
        self.buffer_size = buffer_size
        if rshunt == 0:
            # no override value passed, try to get it from device
            if dev.attrs.get("in_shunt_resistor"):
                rshunt = int(int(dev.attrs['in_shunt_resistor'].value) / 1000)
                if args.verbose >= 1:
                    print("Reading shunt value from device: %dmOhms" % rshunt)
        else:
            self.shunt_override = True
        if rshunt == 0:
            # force a default value
            rshunt = 100
        self.rshunt = rshunt
        if args.verbose >= 1:
            print("Using shunt value: %dmOhms" % (self.rshunt))

        # Checking other device information through XML-RPC (if available)
        if type(xmlrpc) is not type(None):
            self.meta = xmlrpc.info(threadid)
            if 'power switch' in self.meta:
                self.power_switch = True
            if args.verbose >= 1 and self.meta:
                print("Probe related meta data:")
                for key, elem in list(self.meta.items()):
                    print(("  %s: %s" %(key, elem)))
            if not "name" in self.meta:
                self.meta['name'] = ''
        if args.verbose >= 1:
            print("=====================")

    def run(self):
        self.buf = iio.Buffer(self.dev, self.buffer_size)
        if args.verbose >= 1:
            print("<%s> Starting %s" % (self.dev.id, self.dev.name))
            print("<%s> sample freq from device: %fHz" %(self.dev.id, float(self.dev.attrs['in_sampling_frequency'].value)))
            print("<%s> Creating iio buffer, size = %d samples" % (self.dev.id, self.buffer_size))

        ti_last_start = 0.0
        while self.running:
            ti_start = time.time()

            self.buf.refill()
            ti_iiorefill = time.time()

            # Read and compute timer channel
            acmetime = self.crdict.get("Time").read(self.buf)
            unpack_str = 'q' * (len(acmetime) // struct.calcsize('q'))
            val_time = struct.unpack(unpack_str, acmetime)
            # do not apply scale on time
            # val_time = np.asarray(val_time) * scaledict.get("Time")
            val_time = np.asarray(val_time)
            # print "Read %d samples" % (len(val_time)) # reads a complete buffer each time
            if not args.norelatime:
                if self.first_run:
                    self.abs_start_time = val_time[0]
                val_time = val_time - self.abs_start_time

            # convert time from ns to ms (requires conversion from int to float - makes a table copy...)
            val_time = val_time.astype(int) / 1000000

            # Read channels and compute power on this bufer
            vshunt = self.crdict.get("Vshunt").read(self.buf)
            unpack_str = 'h' * (len(vshunt) // struct.calcsize('h'))
            val_vshunt = struct.unpack(unpack_str, vshunt)
            val_vshunt = np.asarray(val_vshunt) * self.scaledict.get("Vshunt")
            if self.enadict.get('Vbat') == True:
                vbat = self.crdict.get("Vbat").read(self.buf)
                val_vbat = struct.unpack(unpack_str, vbat)
                val_vbat = np.asarray(val_vbat) * self.scaledict.get("Vbat")
            else:
                # Use fixed value instead
                val_vbat = np.full(len(val_vshunt), int(self.vbat * 1000), dtype=int)

            if self.ishunt:
                # Compute Ishunt (in mA : 1000x mV / mO) instead of power
                val_power = (val_vshunt * 1000) / self.rshunt
            else:
                # compute power using minimal data (Vbat and Vshunt - we know Rshunt)
                # compute value in mW (mV x mV / mO)
                val_power = (val_vshunt * val_vbat) / self.rshunt

            if args.verbose >= 3:
                print("<%s>  Time (ns => ms) -------------------- " % (self.dev.id))
                print(val_time)
                print("<%s>  Vbat (mV) -------------------- " % (self.dev.id))
                print(val_vbat)
                print("<%s>  Vshunt (mV) -------------------- " % (self.dev.id))
                print(val_vshunt)
                if self.ishunt:
                    print("<%s>  Ishunt (mA) -------------------- " % (self.dev.id))
                else:
                    print("<%s>  Power (mW) -------------------- " % (self.dev.id))
                print(val_power)

            data_thread_lock.acquire()
            # Try to detect discontinuities
            if not self.first_run:
                # Compute buffer time since last buffer
                if args.meancapture:
                    last_buf_time = self.data[self.capture_index - 1, 0]
                    last_delta = val_time.mean(0) - last_buf_time
                    capture_buf_period = val_time[val_time.shape[0] - 1] - val_time[0]
                    # if last buffer is farther than 1 buffer length + 5%, trigger a warning
                    if last_delta > 1.05 * capture_buf_period:
                        missed_samples = int(((last_delta - capture_buf_period) * self.sampling_freq) / 1000)
                        print("<%s> ** Warning: data overflow (and loss - %d samples - %dms) suspected!" % (self.dev.id, missed_samples, last_delta - capture_buf_period))
                        print("<%s> ** last buf: %f, new buf: %f, diff(ms): %f, last bug length (ms): %f" %(self.dev.id, last_buf_time, val_time.mean(0), last_delta, capture_buf_period))
                else:
                    last_buf_time = val_time[0] - self.data[self.data.shape[0] - 1, 0]
                    # trigger a warning if last time buffer is longer than 6 expected periods
                    if last_buf_time > 6 * 1000/self.sampling_freq:
                        missed_samples = int((last_buf_time * self.sampling_freq) / 1000)
                        print("<%s> ** Warning: data overflow (and loss - %d samples) suspected!" % (self.dev.id, missed_samples))
                        print("<%s> ** last buf: %f, new buf: %f, diff(ms): %f, period (ms): %f" %(self.dev.id, self.data[self.data.shape[0] - 1, 0], val_time[0], last_buf_time, 1000/self.sampling_freq))
            ti_iioextract = time.time()

            # add new captured points to table
            if args.meancapture:
                # Compute and store capture buffers mean values only
                # check space for storing new sample
                if self.capture_index >= self.data.shape[0]:
                    # increase buffer size
                    if args.verbose >= 2:
                        print("<%s>  Increasing receive buffer size\n" % (self.dev.id))
                    tmp = self.data
                    self.data = np.empty((self.data.shape[0] + self.buffer_size, 3))
                    self.data[:tmp.shape[0]] = tmp
                # store new sample
                self.data[self.capture_index] = [val_time.mean(0), val_power.mean(0), val_vbat.mean(0)]
                self.capture_index += 1
            else:
                tmp = self.data
                self.data = np.empty((self.data.shape[0] + self.buffer_size, 3))
                self.data[:tmp.shape[0]] = tmp
                self.data[tmp.shape[0]:, 0] = val_time
                self.data[tmp.shape[0]:, 1] = val_power
                self.data[tmp.shape[0]:, 2] = val_vbat
            # Compute and store power mean value on received buffer
            self.meandata = np.append(self.meandata, [[ (val_time[0] + val_time[-1])/2, val_power.mean(), val_vbat.mean() ]], axis=0)
            data_thread_lock.release()
            ti_cpdata = time.time()
            if args.verbose >= 3:
                print("<%s>  mean power (mW) -------------------- " % (self.dev.id))
                print(self.meandata.shape)
                print(self.meandata)

            estimated_freq = (1000 * self.buffer_size) / (val_time[val_time.shape[0] - 1 ] - val_time[0])
            if args.verbose >= 2:
                print("<%s>  iiorefill: %f; iioextract: %f; cpdata: %f; total: %f; (since last: %f) Freq: %.1fHz" % \
            (self.dev.id, ti_iiorefill - ti_start, ti_iioextract - ti_iiorefill, ti_cpdata - ti_iioextract, \
            ti_cpdata - ti_start, ti_start - ti_last_start, estimated_freq))
            if not self.first_run:
                # add last period element time in ms
                self.sample_period_stats = np.append(self.sample_period_stats, (ti_start - ti_last_start) * 1000)
                #only keep the last 10 period values
                self.sample_period_stats = self.sample_period_stats[-10:]
                # compute period mean
                self.sample_period_stats_mean = self.sample_period_stats.mean(0)
                # print self.sample_period_stats[-10:]
                # print "period: ", self.sample_period_stats_mean
                # print self.sample_period_stats
                self.estimated_freq = estimated_freq
            ti_last_start = ti_start

            if self.first_run:
                self.first_run = False


if args.vbat:
    print(("Do not measure Vbat from ACME, and use a fixed Vbat value (%.3fV) to measure power" % (args.vbat)))
    enadict['Vbat'] = False

def setup_ishunt():
    dispstr['pwr_ishunt_str'] = 'Ishunt (mA)'
    dispstr['pwr_plot_str'] = 'Ishunt plot'
    dispstr['pwr_color_str'] = 'Ishunt color'
    dispvars['display Ishunt'] = True

if args.ishunt:
    if not args.load and not args.template:
        setup_ishunt()
    else:
        print("Ignoring ishunt option (using settings from loaded acme file)")

if args.load:
    print("Reading %s file..." % (args.load))
    pkl_file = open(args.load, 'rb')
    dispvars = pickle.load(pkl_file)
    databufs = pickle.load(pkl_file)
    if args.timeoffset:
        # update data points
        for t in databufs:
            gdata = t['gdata'][0:t['plotindex']]
            gdata[:,0] += args.timeoffset
        # Update zoom window visible range
        dispvars['zoom range'] = list(dispvars['zoom range'])
        for i,t in enumerate(dispvars['zoom range']):
            t += args.timeoffset
            dispvars['zoom range'][i] = t

    # Keep backward compatibility with files without added fields
    for i, t in enumerate(databufs):
        if 'mdata' not in t:
            if args.verbose >= 2:
                print("mdata not found, creating it")
            t['mdata'] = np.empty((0,3))
    if args.verbose >= 2:
        print("Loaded data:")
        print(databufs)
    pkl_file.close()

if args.template:
    print("Reading %s file..." % (args.template))
    pkl_file = open(args.template, 'rb')
    dispvars = pickle.load(pkl_file)
    pkl_file.close()
    tmpl_setup = True

if dispvars['display Ishunt'] == True:
    # May have loaded Ishunt setup from file, so make sure to apply to it
    # to capture and / or menus
    args.ishunt = True
    setup_ishunt()

if not args.load:
    print("Connecting with ACME...")
    # IIO inits
    try:
        if args.ip:
            print("  Connecting with IP address: ", args.ip)
            ctx = iio.Context("ip:" + args.ip)
            acme_address = args.ip
        else:
            print("  Connecting using iio fallback (IIOD_REMOTE=<%s>)" % (os.environ['IIOD_REMOTE']))
            ctx = iio.Context()
            acme_address = os.environ['IIOD_REMOTE']
    except:
        print("ERROR creating ACME iio context, aborting.")
        sys.exit()

    if args.inttime == None:
        # option without arguments: fetch expected values, print them and exit
        print("  Please, use one of the following integration times:")
        print("    ", ctx.devices[0].attrs['integration_time_available'].value)
        sys.exit()
    elif args.inttime:
        # try to use parameter passed
        if args.inttime in ctx.devices[0].attrs['integration_time_available'].value.split(' '):
            integration_time = args.inttime
            if args.verbose >= 1:
                print("Using passed integration time: ", integration_time)
        else:
            print("Wrong integration time passed (%s), leaving..." % (args.inttime))
            print("Please, use one of the following integration times:")
            print("  ", ctx.devices[0].attrs['integration_time_available'].value)
            sys.exit()

    # Get per channel shunt values, if provided
    # shunts table only used to pass override value (if any) at device init
    shunts = [ 0 ] * len(ctx.devices)   # 0 to not override shunt value
    if args.shunts:
        # get list of shunts from command-line and convert it to a list of int
        pshunt = list(map(int, args.shunts.split(',')))
        # note that parameter list may be incomplete, so make sure shunts is padded with enough 0s
        shunts[0:len(pshunt)-1] = pshunt
    if args.verbose >= 2:
        print("  Using following shunts values, per device: ", shunts)

    # Try to use XMLRPC service with ACME
    print (probe)
    acme_xmlrpc = acmeXmlrpc(acme_address, probe)

    # Create threads: 1 for each ACME detected device
    data_thread_lock = threading.Lock() # Lock used for any shared data buffer access
    threads = []
    thread_id = 0
    print (ctx.devices)
    devices = sorted(ctx.devices, key=lambda device: int(device.id[10:]))
    print (devices)
    if probe:
        thread = deviceThread(thread_id, devices[0], shunts[thread_id], 1,
                            enadict, args.vbat, args.ishunt, acme_xmlrpc)
        threads.append(thread)
        databufs.append({'gdata' : np.empty((0,3)), 'mdata' : np.empty((0,3)), 'deviceid' : devices[0].id, 'devicename' : devices[0].name,
                        'name' : thread.meta['name'], 'plotindex' : 0})
    else:
        for d in devices:
            thread = deviceThread(thread_id, d, shunts[thread_id], len(ctx.devices),
                                enadict, args.vbat, args.ishunt, acme_xmlrpc)
            threads.append(thread)
            databufs.append({'gdata' : np.empty((0,3)), 'mdata' : np.empty((0,3)), 'deviceid' : d.id, 'devicename' : d.name,
                            'name' : thread.meta['name'], 'plotindex' : 0})
            thread_id += 1
    # print databufs
    # sys.exit()

    # Startup all threads after setup, so that sampling rates are consolidated
    if args.verbose >= 2:
        print(threads)
    for thread in threads:
        thread.start()


def update_display():
    ti_start = time.time()
    if threads[0].first_run:
        # avoid boarder effects with plots if tables are empty
        return

    data_thread_lock.acquire()
    for i, t in enumerate(threads):
        # thread.gdata = np.copy(thread.data)
        # Make sure we make a deep copy of samples
        # we do not want to acces t.data outside of the lock
        databufs[i]['gdata'] = np.empty_like(t.data)
        databufs[i]['gdata'][:] = t.data
        databufs[i]['mdata'] = np.empty_like(t.meandata)
        databufs[i]['mdata'][:] = t.meandata
        if args.meancapture:
                databufs[i]['plotindex'] = t.capture_index
    data_thread_lock.release()
    for i, t in enumerate(databufs):
        mean_data = t['gdata'].mean(axis=0)
        with open(filename, "a") as f:
            f.write("%d,%.3f,%.3f\n" %(int(t['gdata'][-1][0]/1000), mean_data[2], mean_data[1]))

if args.verbose >= 1:
    print("Starting live capture mode...")
while True:
    update_display()
    time.sleep(samplingtime)

if __name__ == '__main__':
    import sys

    if  not args.load:
        if args.verbose >= 2:
            print("Stopping threads...")
        for t in threads:
            t.running = False
        for t in threads:
            t.join()

    #
    # Usage for Power Data Logging:
    # sudo python3 pyacmegraph.py --ip <BeagleBoneBlack IP Addresss> --ishunt --norelatime --filename <data logging csv filename> --samplingtime <data logging interval in seconds>
    #
    # Example:
    # sudo python3 pyacmegraph.py --ip 172.16.2.127 --ishunt --norelatime --filename power_measurement_data.csv --samplingtime 1
    #