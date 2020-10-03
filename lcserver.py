#!/usr/bin/python
# vim: set ts=4 sw=4 et :
#
# lcserver.py - LabControl server CGI script
#
# Copyright 2020 Sony
#
# Implementation notes:
#  data directory = place where json object files are stored
#  files directory = place where file bundles are stored
#  pages directory = place where web page templates are stored
#
#
# The server implements both:
#  1. the human user interace (web pages showing the object store)
#  2. the computer ReST interface (used for sending, modifying and
#     retrieving the data in the store)
#
# The server currently supports the top-level "pages":
#   boards, requests, resources logs
#
# currently, the lc command uses request to send urls like:
#  POST /lcserver.py/?action=query_objects
#  I'm calling this the 'action api'
#
# The labcontrol API specified with Timesys uses urls like this:
#  /api/devices/bbb/power/reboot
# I'm calling this the 'path api'
#
# To do:
# - actions:
#   - start with power-control = power-controller/reboot
# - queries:
#   - handle regex wildcards instead of just start/end wildcards
# - objects:
#   - support board registration - put-board
#   - support resource registration - put-resource
#   - support host registration
#   - support user registration
# - requests:
# - security:
#   - add otp authentication to all requests
#     - check host's otp file for specified key
#     - erase key after use
# - add hosts (or users)
#    - so we can: 1) save an otp file, 2) validate requests?
# - see also items marked with FIXTHIS
#

import sys
import os
import time
import cgi
import re
import tempfile
# import these as needed
#import json
#import yaml

try:
    from subprocess import getstatusoutput
except:
    from commands import getstatusoutput

VERSION=(0,6,0)

# precedence of installation locations:
# 2. local lcserver in Fuego container
# 3. test lcserver on Tim's private server machine (birdcloud.org)
# 4. test lcserver on Tim's home desktop machine (timdesk)
base_dir = "/home/ubuntu/work/labcontrol/lc-data"
if not os.path.exists(base_dir):
    base_dir = "/usr/local/lib/labcontrol/lc-data"
if not os.path.exists(base_dir):
    base_dir = "/home/tbird/work/labcontrol/lc-data"

# this is used for debugging only
def log_this(msg):
    with open(base_dir+"/lcserver.log" ,"a") as f:
        f.write("[%s] %s\n" % (get_timestamp(), msg))

# define an instance to hold config vars
class config_class:
    def __init__(self):
        pass

    def __getitem__(self, name):
        return self.__dict__[name]

config = config_class()
config.data_dir = base_dir + "/data"

# crude attempt at auto-detecting url_base
if os.path.exists("/usr/lib/cgi-bin/lcserver.py"):
    config.url_base = "/cgi-bin/lcserver.py"
else:
    config.url_base = "/lcserver.py"

config.files_url_base = "/lc-data"
config.files_dir = base_dir + "/files"
config.page_dir = base_dir + "/pages"

class req_class:
    def __init__(self, config, form):
        self.config = config
        self.header_shown = False
        self.message = ""
        self.page_name = ""
        self.page_url = "page_name_not_set_error"
        self.form = form
        self.html = []

    def set_page_name(self, page_name):
        page_name = re.sub(" ","_",page_name)
        self.page_name = page_name
        self.page_url = self.make_url(page_name)

    def page_filename(self):
        if not hasattr(self, "page_name"):
            raise AttributeError, "Missing attribute"
        return self.config.page_dir+os.sep+self.page_name

    def read_page(self, page_name=""):
        if not page_name:
            page_filename = self.page_filename()
        else:
                page_filename = self.config.page_dir+os.sep+page_name

        return open(page_filename).read()

    def make_url(self, page_name):
        page_name = re.sub(" ","_",page_name)
        return self.config.url_base+"/"+page_name

    def html_escape(self, str):
        str = re.sub("&","&amp;",str)
        str = re.sub("<","&lt;",str)
        str = re.sub(">","&gt;",str)
        return str

    def add_to_message(self, msg):
        self.message += msg + "<br>\n"

    def add_msg_and_traceback(self, msg):
        self.add_to_message(msg)
        import traceback
        tb = traceback.format_exc()
        self.add_to_message("<pre>\n%s\n</pre>\n" % tb)

    def show_message(self):
        if self.message:
            self.html.append("<h2>lcserver message(s):</h2>")
            self.html.append(self.message)

    def show_header(self, title):
        if self.header_shown:
            return

        self.header = """Content-type: text/html\n\n"""

        # render the header markup
        self.html.append(self.header)
        self.html.append('<body><h1 align="center">%s</h1>' % title)
        self.header_shown = True

    def show_footer(self):
        self.show_message()
        self.html.append("</body>")

    def html_error(self, msg):
        return "<font color=red>" + msg + "</font><BR>"

    def send_response(self, result, data):
        self.html.append("Content-type: text/plain\n\n%s\n" % result)
        self.html.append(data)

# end of req_class
#######################

def show_env(req, env, full=0):
    env_keys = env.keys()
    env_keys.sort()

    env_filter=["PATH_INFO", "QUERY_STRING", "REQUEST_METHOD", "SCRIPT_NAME"]
    req.html.append("Here is the environment:")
    req.html.append("<ul>")
    for key in env_keys:
        if full or key in env_filter:
            req.html.append("<li>%s=%s" % (key, env[key]))
    req.html.append("</ul>")

def get_timestamp():
    t = time.time()
    tfrac = int((t - int(t))*100)
    timestamp = time.strftime("%Y-%m-%d_%H:%M:%S.") + "%02d" % tfrac
    return timestamp

def save_file(req, file_field, upload_dir):
    # some debugging...
    F = "FAIL"
    msg = ""

    #msg += "req.form=\n"
    #for k in req.form.keys():
    #   msg += "%s: %s\n" % (k, req.form[k])

    if not req.form.has_key(file_field):
        return F, msg+"Form is missing key %s\n" % file_field, ""

    fileitem = req.form[file_field]
    if not fileitem.file:
        return F, msg+"fileitem has no attribute 'file'\n", ""

    if not fileitem.filename:
        return F, msg+"fileitem has no attribute 'filename'\n", ""

    filepath = upload_dir + os.sep +  fileitem.filename
    if os.path.exists(filepath):
        return F, msg+"Already have a file %s. Cannot proceed.\n" % fileitem.filename, ""

    fout = open(filepath, 'wb')
    while 1:
        chunk = fileitem.file.read(100000)
        if not chunk:
            break
        fout.write(chunk)
    fout.close()
    msg += "File '%s' uploaded successfully!\n" % fileitem.filename
    return "OK", msg, filepath


def do_put_object(req, obj_type):
    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    result = "OK"
    msg = ""

    # convert form (cgi.fieldStorage) to dictionary
    try:
        obj_name = req.form["name"].value
    except:
        result = "FAIL"
        msg += "Error: missing %s name in form data" % obj_type
        req.send_response(req, result, msg)
        return

    obj_dict = {}
    for k in req.form.keys():
        obj_dict[k] = req.form[k].value

    # sanity check the submitted data
    for field in required_put_fields[obj_type]:
        try:
            value = obj_dict[field]
        except:
            result = "FAIL"
            msg += "Error: missing required field '%s' in form data" % field
            break

        # FIXTHIS - for cross references (board, resource), check that these
        # are registered with the server
        # here is an example:
        # see if a referenced board is registered with the server
        #if field.startswith("board") or field.endswith("board"):
        #    board_filename = "board-%s.json" % (value)
        #    board_data_dir = req.config.data_dir + os.sep + "boards"
        #    board_path = board_data_dir + os.sep + board_filename
        #
        #    if not os.path.isfile(board_path):
        #        result = "FAIL"
        #        msg += "Error: No matching board '%s' registered with server (from field '%s')" % (value, field)
        #        break

    if result != "OK":
        req.send_response(req, result, msg)
        return

    req_result = None
    if obj_type == "request":
        obj_dict["state"] = "pending"
        timestamp = get_timestamp()
        obj_name += "-" + timestamp
        obj_dict["name"] = obj_name

    filename = obj_type + "-" + obj_name
    jfilepath = data_dir + os.sep + filename + ".json"

    # convert to json and save to file
    import json
    data = json.dumps(obj_dict, sort_keys=True, indent=4,
            separators=(',', ': '))
    fout = open(jfilepath, "w")
    fout.write(data+'\n')
    fout.close()

    msg += "%s accepted (filename=%s)\n" % (obj_name, filename)

    if obj_type == "request":
        req_result, req_msg = process_request(req, obj_dict)
        msg += "%s\n%s" % (req_result, req_msg)

    req.send_response(req, result, msg)

# define an array with the fields that allowed to be modified
# for each different object type:
allowed_update_fields = {
    "board": ["state", "kernel_version", "reservation"],
    "request": ["state", "start_time", "done_time"],
    "resource": ["state", "reservation", "command"]
    }

# Update board, resource and request objects
def do_update_object(req, obj_type):
    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    msg = ""

    try:
        obj_name = req.form[obj_type].value
    except:
        msg += "Error: can't read %s from form" % obj_type
        req.send_response("FAIL", msg)
        return

    filename = obj_type + "-" + obj_name + ".json"
    filepath = data_dir + os.sep + filename
    if not os.path.exists(filepath):
        msg += "Error: filepath %s does not exist" % filepath
        req.send_response("FAIL", msg)
        return

    # read requested object file
    import json
    fd = open(filepath, "r")
    obj_dict = json.load(fd)
    fd.close()

    # update fields from (cgi.fieldStorage)
    for k in req.form.keys():
        if k in [obj_type, "action"]:
            # skip these
            continue
        if k in allowed_update_fields[obj_type]:
            # FIXTHIS - could check the data input here
            obj_dict[k] = req.form[k].value
        else:
            msg = "Error - can't change field '%s' in %s %s (not allowed)" % \
                    (k, obj_type, obj_name)
            req.send_response("FAIL", msg)
            return

    # put dictionary back in json format (beautified)
    data = json.dumps(obj_dict, sort_keys=True, indent=4,
            separators=(',', ': '))
    fout = open(filepath, "w")
    fout.write(data+'\n')
    fout.close()

    req.send_response("OK", data)

# try matching with simple wildcards (* at start or end of string)
def item_match(pattern, item):
    if pattern=="*":
        return True
    if pattern==item:
        return True
    if pattern.endswith("*") and \
        pattern[:-1] == item[:len(pattern)-1]:
        return True
    if pattern.startswith("*") and \
        pattern[1:] == item[-(len(pattern)-1):]:
        return True
    return False

def do_query_objects(req):
    try:
        obj_type = req.form["obj_type"].value
    except:
        msg = "Error: can't read object type ('obj_type') from form"
        req.send_response("FAIL", msg)
        return

    if obj_type not in ["board", "resource", "request"]:
        msg = "Error: unsupported object type '%s' for query" % obj_type
        req.send_response("FAIL", msg)
        return

    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    msg = ""

    filelist = os.listdir(data_dir)
    filelist.sort()

    # can query by different fields
    # obj_name is in the filename so we don't need to open the json file
    #   in order to filter by it.
    # other fields are inside the json and requiring opening each file

    try:
        query_obj_name = req.form["name"].value
    except:
        query_obj_name = "*"

    # handle name-based queries
    match_list = []
    for f in filelist:
        prefix = obj_type + "-"
        if f.startswith(obj_type + "-") and f.endswith(".json"):
            file_obj_name = f[len(prefix):-5]
            if not file_obj_name:
                continue
            if not item_match(query_obj_name, file_obj_name):
                continue
            match_list.append(file_obj_name)

    # FIXTHIS - read files and filter by attributes
    # particularly filter on 'state'

    for obj_name in match_list:
       msg += obj_name+"\n"

    req.send_response("OK", msg)


def old_do_query_requests(req):
    #log_this("in do_query_requests")
    req_data_dir = req.config.data_dir + os.sep + "requests"
    msg = ""

    filelist = os.listdir(req_data_dir)
    filelist.sort()

    # can query by different fields, some in the name and some inside
    # the json

    try:
        query_host = req.form["host"].value
    except:
        query_host = "*"

    try:
        query_board = req.form["board"].value
    except:
        query_board = "*"

    # handle host and board-based queries
    match_list = []
    for f in filelist:
        if f.startswith("request-") and f.endswith("json"):
            host_and_board = f[31:-5]
            if not host_and_board:
                continue
            if not item_match(query_host, host_and_board.split(":")[0]):
                continue
            if not item_match(query_board, host_and_board.split(":")[1]):
                continue
            match_list.append(f)

    # read files and filter by attributes
    # (particularly filter on 'state')
    if match_list:
        import json

        # read the first file to get the list of possible attributes
        f = match_list[0]
        with open(req_data_dir + os.sep + f) as jfd:
            data = json.load(jfd)
            # get a list of valid attributes
            fields = data.keys()

            # get rid of fields already processed
            fields.remove("host")
            fields.remove("board")

        # check the form for query attributes
        # if they have the same name as a valid field, then add to list
        query_fields={}
        for field in fields:
            try:
                query_fields[field] = req.form[field].value
            except:
                pass

        # if more to query by, then go through files, preserving matches
        if query_fields:
            ml_tmp = []
            for f in match_list:
                drop = False
                with open(req_data_dir + os.sep + f) as jfd:
                    data = json.load(jfd)
                    for field, pattern in query_fields.items():
                        if not item_match(pattern, str(data[field])):
                            drop = True
                if not drop:
                    ml_tmp.append(f)
            match_list = ml_tmp

    for f in match_list:
        # remove .json extension from request filename, to get the req_id
        req_id = f[:-5]
        msg += req_id+"\n"

    req.send_response("OK", msg)

# FIXTHIS - could do get_next_request (with wildcards) to save a query
def do_get_request(req):
    req_data_dir = req.config.data_dir + os.sep + "requests"
    msg = ""

    # handle host and target-based queries
    msg += "In lcserver.py:get_request\n"
    try:
        request_id = req.form["request_id"].value
    except:
        msg += "Error: can't read request_id from form"
        req.send_response("FAIL", msg)
        return

    filename = request_id + ".json"
    filepath = req_data_dir + os.sep + filename
    if not os.path.exists(filepath):
        msg += "Error: filepath %s does not exist" % filepath
        req.send_response("FAIL", msg)
        return

    # read requested file
    import json
    request_fd = open(filepath, "r")
    mydict = json.load(request_fd)

    # beautify the data, for now
    data = json.dumps(mydict, sort_keys=True, indent=4, separators=(',', ': '))
    req.send_response("OK", data)

def do_remove_object(req, obj_type):
    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    msg = ""

    try:
        obj_name = req.form[obj_type].value
    except:
        msg += "Error: can't read '%s' from form" % obj_type
        req.send_response("FAIL", msg)
        return

    filename = obj_name + ".json"
    filepath = data_dir + os.sep + filename
    if not os.path.exists(filepath):
        msg += "Error: filepath %s does not exist" % filepath
        req.send_response("FAIL", msg)
        return

    # FIXTHIS - should check permissions here
    # only original-submitter and resource-host are allowed to remove
    os.remove(filepath)

    msg += "%s %s was removed" % (obj_type, obj_name)
    req.send_response("OK", msg)

def file_list_html(req, file_type, subdir, extension):
    if file_type == "files":
        src_dir = req.config.files_dir + os.sep + subdir
    elif file_type == "data":
        src_dir = req.config.data_dir + os.sep + subdir
    elif file_type == "page":
        src_dir = req.config.page_dir
    else:
        raise ValueError("cannot list files for file_type %s" % file_type)

    full_dirlist = os.listdir(src_dir)
    full_dirlist.sort()

    # filter list to only ones with requested extension
    filelist = []
    for d in full_dirlist:
        if d.endswith(extension):
            filelist.append(d)

    if not filelist:
        return req.html_error("No %s (%s) files found." % (subdir[:-1], extension))

    files_url = "%s/%s/%s/" % (config.files_url_base, file_type, subdir)
    html = "<ul>"
    for item in filelist:
        html += '<li><a href="'+files_url+item+'">' + item + '</a></li>\n'
    html += "</ul>"
    return html

def show_request_table(req):
    src_dir = req.config.data_dir + os.sep + "requests"

    full_dirlist = os.listdir(src_dir)
    full_dirlist.sort()

    # filter list to only request....json files
    filelist = []
    for f in full_dirlist:
        if f.startswith("request") and f.endswith(".json"):
            filelist.append(f)

    if not filelist:
        return req.html_error("No request files found.")

    files_url = config.files_url_base + "/data/requests/"
    html = """<table border="1" cellpadding="2">
  <tr>
    <th>Request</th>
    <th>State</th>
    <th>Requestor</th>
    <th>Host</th>
    <th>Board</th>
    <th>Test</th>
    <th>Run (results)</th>
  </tr>
"""
    import json
    for item in filelist:
        request_fd = open(src_dir+os.sep + item, "r")
        req_dict = json.load(request_fd)
        request_fd.close()

        # add data, in case it's missing
        try:
            run_id = req_dict["run_id"]
        except:
            req_dict["run_id"] = "Not available"

        html += '  <tr>\n'
        html += '    <td><a href="'+files_url+item+'">' + item + '</a></td>\n'
        for attr in ["state", "requestor", "host", "board", "test_name",
                "run_id"]:
            html += '    <td>%s</td>\n' % req_dict[attr]
        html += '  </tr>\n'
    html += "</table>"
    req.html.append(html)

def do_show(req):
    req.show_header("Lab Control objects")
    #log_this("in do_show, req.page_name='%s'\n" % req.page_name)
    #req.html.append("req.page_name='%s' <br><br>" % req.page_name)

    if req.page_name not in ["boards", "resources", "requests", "logs", "main"]:
        title = "Error - unknown object type '%s'" % req.page_name
        req.add_to_message(title)
    else:
        if req.page_name=="boards":
            req.html.append("<H1>List of boards</h1>")
            req.html.append(file_list_html(req, "data", "boards", ".json"))
        elif req.page_name == "resources":
            req.html.append("<H1>List of resources</h1>")
            req.html.append(file_list_html(req, "data", "resources", ".json"))
        elif req.page_name == "requests":
            req.html.append("<H1>Table of requests</H1>")
            show_request_table(req)
        elif req.page_name == "logs":
            req.html.append("<H1>Table of logs</H1>")
            req.html.append(file_list_html(req, "files", "logs", ".txt"))

    if req.page_name != "main":
        req.html.append("<br><hr>")

    req.html.append("<H1>Lab Control objects on this server</h1>")
    req.html.append("""
Here are links to the different Lab Control objects:<br>
<ul>
<li><a href="%(url_base)s/boards">Boards</a></li>
<li><a href="%(url_base)s/resources">Resources</a></li>
<li><a href="%(url_base)s/requests">Requests</a></li>
<li><a href="%(url_base)s/logs">Logs</a></li>
</ul>
<hr>
""" % req.config )

    req.html.append("""<a href="%(url_base)s">Back to home page</a>""" % req.config)

    req.show_footer()

def get_object_list(req, obj_type):
    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    obj_list = []

    filelist = os.listdir(data_dir)
    prefix = obj_type+"-"
    for f in filelist:
        if f.startswith(prefix) and f.endswith(".json"):
            # remove board- and '.json' to get the board_name
            obj_name = f[len(prefix):-5]
            obj_list.append(obj_name)

    obj_list.sort()
    return obj_list


# supported api actions by path:
# devices = list boards
# devices/{board} = show board data (json file data)
# devices/{board}/status = show board status
# devices/{board}/reboot = reboot board
# resources = list resources
# resources/{resource} = show resource data (json file data)


def return_api_object_list(req, obj_type):
    obj_list = get_object_list(req, obj_type)

    msg = ""
    for obj_name in obj_list:
        msg += obj_name + "\n"

    req.send_response("OK", msg)

def get_object_data(req, obj_type, obj_name):
    filename = obj_type + "-" + obj_name + ".json"
    file_path = "%s/%ss/%s-%s.json" %  (req.config.data_dir, obj_type, obj_type, obj_name)

    #msg = "in get_object_data - file_path is '%s'" % file_path
    #req.send_response("INFO", msg)

    if not os.path.isfile(file_path):
        msg = "%s object '%s' in not recognized by the server" % (obj_type, obj_name)
        msg += "- file_path was '%s'" % file_path
        req.send_response("FAIL", msg)
        return {}

    data = ""
    try:
        data = open(file_path, "r").read()
    except:
        msg = "Could not retrieve information for %s '%s'" % (obj_type, obj_name)
        msg += "- file_path was '%s'" % file_path
        req.send_response("FAIL", msg)
        return {}

    return data

def get_object_map(req, obj_type, obj_name):
    data = get_object_data(req, obj_type, obj_name)
    if not data:
        return {}
    try:
        import json
        obj_map = json.loads(data)
    except:
        msg = "Invalid json detected in %s '%s'" % (obj_type, obj_name)
        msg += "\njson='%s'" % data
        req.send_response("FAIL", msg)
        return {}

    return obj_map

def get_connected_resource(req, board_map, resource_type):
    # look up connected resource type in board map
    resource = board_map.get(resource_type, None)
    if not resource:
        msg = "Could not find a %s resource connected to board '%s'" % (resource_type, board)
        req.send_response("FAIL", msg)
        return None

    resource_map = get_object_map(req, "resource", resource)
    return resource_map

def return_api_object_data(req, obj_type, obj_name):
    # do default action for an object - return json file data (as a string)
    data = get_object_data(req, obj_type, obj_name)
    if not data:
        return

    req.send_response("OK", data)

# execute a resource command
def exec_command(req, board_map, resource_map, res_cmd):
    # lookup command to execute in resource_map
    res_cmd_str = res_cmd + "_cmd"
    if res_cmd_str not in resource_map:
        msg = "Resource '%s' does not have %s attribute, cannot execute" % (resource["name"], res_cmd_str)
        req.send_response("FAIL", msg)
        return

    cmd_str = resource_map[res_cmd_str]
    rcode, result = getstatusoutput(cmd_str)
    if rcode:
        msg = "Result of %s operation on resource %s = %d" % (res_cmd, resource["name"], rcode)
        msg += "command output='%s'" % result
        req.send_response("FAIL", msg)
        return

    req.send_response("OK", "")

# rest is a list of the rest of the path
def return_api_board_action(req, board, action, rest):
    if board not in get_object_list(req, "board"):
        msg = "Could not find board '%s' registered with server" % board
        req.send_response("FAIL", msg)
        return

    board_map = get_object_map(req, "board", board)
    if not board_map:
        return

    if action == "power":
        pdu_map = get_connected_resource(req, board_map, "power-controller")
        if not pdu_map:
            return

        if not rest:
            #do_resource_action(pdu_map, board, "status")
            msg = "power status not supported"
            req.send_response("FAIL", msg)
            return
        elif rest[0] == "on":
            #do_resource_action(pdu_map, board, "on")
            msg = "power on not supported"
            req.send_response("FAIL", msg)
            return
        elif rest[0] == "off":
            msg = "power off not supported"
            req.send_response("FAIL", msg)
            return
        elif rest[0] == "reboot":
            msg = "power reboot not supported"
            msg += " - but found power controller '%s'" % pdu_map["name"]
            req.send_response("FAIL", msg)
            exec_command(req, board_map, pdu_map, "reboot")
            return
        else:
            msg = "power action '%s' not supported" % rest[0]
            req.send_response("FAIL", msg)
            return

    msg = "action '%s' not supported (rest='%s')" % (action, rest)
    req.send_response("FAIL", msg)

def do_api(req):
    # determine api operation from path
    req_path = req.environ.get("PATH_INFO", "")
    path_parts = req_path.split("/")
    # get the path elements after 'api'
    parts = path_parts[path_parts.index("api")+1:]

    #req.show_header("in do_api")
    #req.html.append("parts=%s" % parts)

    if not parts:
        msg = "Invalid empty path after /api"
        req.send_response("FAIL", msg)
        return

    if parts[0] == "devices":
        if len(parts) == 1:
            # handle /api/devices - list devices
            # list devices (boards)
            return_api_object_list(req, "board")
            return
        else:
            board = parts[1]
            if len(parts) == 2:
                # handle api/device/{board}
                return_api_object_data(req, "board", board)
                return
            else:
                action = parts[2]
                rest = parts[3:]
                return_api_board_action(req, board, action, rest)
                return
        return
    elif parts[0] == "resources":
        if len(parts) == 1:
            # handle /api/resources - list resources
            # list devices
            return_api_object_list(req, "resource")
            return
        else:
            resource = parts[1]
            if len(parts) == 2:
                # handle api/resource/{resource}
                return_api_object_data(req, "resource", resource)
                return
            else:
                action = parts[2]
                rest = parts[3:]
                msg = "Unsupported elements '%s/%s' after /api/resources" % (action, "/".join(rest))
                req.send_response("FAIL", msg)
                return
    else:
        msg = "Unsupported element '%s' after /api/" % parts[0]
        req.send_response("FAIL", msg)
        return

    req.html.append("""<a href="%(url_base)s">Back to home page</a>""" % req.config)

    req.show_footer()

def handle_request(environ, req):
    req.environ = environ

    # determine action, if any
    action = req.form.getfirst("action", "show")
    #req.add_to_message('action="%s"<br>' % action)
    req.action = action

    # get page name (last element of path)
    req_path = environ.get("PATH_INFO", "%s/main" % req.config.url_base)
    page_name = os.path.basename(req_path)
    if page_name == os.path.basename(req.config.url_base):
        page_name = "main"
    req.set_page_name(page_name)

    #req.add_to_message("page_name=%s" % page_name)

    # see if /api is in path
    if "api" in req_path.split("/"):
        action = "api"

    #req.add_to_message("api=%s" % api)

    # NOTE: uncomment this when you get a 500 error
    #req.show_header('Debug')
    #show_env(req, environ)
    #show_env(req, environ, True)
    log_this("in main request loop: action='%s', page_name='%s'<br>" % (action, page_name))
    #req.add_to_message("in main request loop: action='%s'<br>" % action)

    # perform action
    action_list = ["show", "api",
            "add_board", "add_resource", "put_request",
            "query_objects",
            "get_board", "get_resource", "get_request",
            "remove_board", "remove_resource", "remove_request",
            "update_board", "update_resource", "update_request",
            "put_log", "get_log"]

    # map action names to "do_<action>" functions
    if action in action_list:
        try:
            action_function = globals().get("do_" + action)
        except:
            msg = "Error: unsupported action '%s' (probably missing a do_%s routine)" % (action, action)
            req.send_response("FAIL", msg)
            return

        action_function(req)
        return

    req.show_header("LabControl server Error")
    req.html.append(req.html_error("Unknown action '%s'" % action))


def cgi_main():
    form = cgi.FieldStorage()

    req = req_class(config, form)

    try:
        handle_request(os.environ, req)
    except SystemExit:
        pass
    except:
        req.show_header("LabControl Server Error")
        req.html.append('<font color="red">Execution raised by software</font>')

        # show traceback information here:
        req.html.append("<pre>")
        import traceback
        (etype, evalue, etb) = sys.exc_info()
        tb_msg = traceback.format_exc()
        req.html.append("traceback=%s" % tb_msg)
        req.html.append("</pre>")

    # output html to stdout
    for line in req.html:
        print(line)

    sys.stdout.flush()

if __name__=="__main__":
    cgi_main()
