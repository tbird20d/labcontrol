#!/bin/sh
#
# serial-receive-test.sh - use lc to perform a serial port test
#
# This is a very simple test, sending data from the lab serial port
# to a serial port on the DUT
#
# outline:
#  discover lab endpoint for the DUT serial connection
#  for each baud-rate:
#    set configuration of DUT serial device
#    set configuration of lab endpoint
#    start capture for DUT endpoint
#    transmit (write data) to the lab serial device
#    stop capture for DUT endpoint
#    compare data - test success or failure
#    delete lab capture
#

start_time=$(date +"%s.%N")

CLIENT="lc"

error_out() {
    echo "Error: $@"
    exit 1
}

# parse arguments
if [ "$1" = "-h" -o "$1" = "--help" ] ; then
    cat <<HERE
Usage: serial-receive-test.sh [-h] [-v] <board> <device_id> <device> <baud_rate1> ...

Transmit data over a serial port to an endpoint off this board.
Arguments:
  <board>      Board name registered with lab server
  <device_id>  String identifier for uart under test (e.g. uart1)
               This is used to find the lab endpoint for the serial connection.
  <device>     Serial device for uart under test (e.g. /dev/ttyS1)
  <baud_rate1> The final arguments are a list of baud rates to test.

Options:
 -h, --help  Show this usage help
 -v          Show verbose output

Test output is in TAP format.  This test requires that a board farm
REST API client be installed on the board (e.g. 'lc' or 'ebf'), and that
the server configuration has previously been set up -- that is, the
client is logged in, the indicated board is reserved, serial hardware
is set up, and the correct configuration for the serial resource
and connection is present on the lab server.
HERE
    exit 0
fi

export VERBOSE=
if [ "$1" = "-v" ] ; then
    export VERBOSE=1
    shift
fi

BOARD="$1"
if [ -z $BOARD ] ; then
    error_out "Missing board argument for serial test"
fi
shift

DEVICE_ID="$1"
if [ -z $DEVICE_ID ] ; then
    error_out "Missing device argument for serial test"
fi
shift

DEVICE="$1"
if [ -z $DEVICE ] ; then
    error_out "Missing device argument for serial test"
fi
shift

export TC_NUM=1

# show a TAP-formatted fail line
# $1 = TESTCASE name
# optional $2 = failure reason
fail() {
    if [ -n "$2" ] ; then
        echo "# $2"
    fi
    echo "not ok $TC_NUM - $1"
    TC_NUM="$(( $TC_NUM + 1 ))"
}

# show a TAP-formatted success line
# $1 = TESTCASE name
# optional $2 = extra diagnostic data
succeed() {
    if [ -n "$2" ] ; then
        echo "# $2"
    fi
    echo "ok $TC_NUM - $1"
    TC_NUM="$(( $TC_NUM + 1 ))"
}

v_echo() {
    if [ -n "$VERBOSE" ] ; then
        echo $@
    fi
}

echo "TAP version 13"
echo "1..$#"

echo "Using client '$CLIENT' with board $BOARD and device $DEVICE_ID ($DEVICE)"

v_echo "Getting resource and lab endpoint for board"

RESOURCE=$($CLIENT $BOARD get-resource serial $DEVICE_ID)
if [ "$?" != "0" ] ; then
    error_out "Could not get resource for $BOARD:$DEVICE_ID"
fi

SEND_DATA="This is an ASCII test string, transmitted over the serial line"

echo "Starting serial transmission test"
v_echo "RESOURCE=$RESOURCE"

test_one_rate() {
    TESTCASE="Check reception at baud-rate $BAUD_RATE"
    v_echo "Configuring for baud rate $BAUD_RATE"

    # Configure baud rate for DUT and lab endpoint
    stty -F $DEVICE $BAUD_RATE raw -echo -echoe -echok
    if [ "$?" != "0" ] ; then
        fail "$TESTCASE" "Could not set baud rate $BAUD_RATE for $BOARD:$DEVICE_ID"
        return
    fi

    # send some configuration JSON to the server
    echo "{ \"baud_rate\": \"$BAUD_RATE\" }" | \
        $CLIENT $RESOURCE set-config serial
    if [ "$?" != "0" ] ; then
        fail "$TESTCASE" "Could not set baud rate $BAUD_RATE for $RESOURCE"
        return
    fi

    v_echo "Capturing data at the board serial port"
    nohup cat $DEVICE >/tmp/serial_received_data &

    # give time for receiver to fully settle - I'm not sure this is needed
    sleep 0.3

    v_echo "Transmitting data from lab"
    echo $SEND_DATA | $CLIENT $RESOURCE serial put-data

    # give time for receiver to receive data - I'm not sure this is needed
    sleep 0.3

    # don't stop if an error occurs trying to kill the 'cat'
    sync
    set +e
    CAT_PID=$(ps aux | grep "[c]at $DEVICE" | cut -b 9-15)
    v_echo "Stopping serial capture (pid=$CAT_PID)"
    if [ -n "$CAT_PID" ] ; then
       kill -9 $CAT_PID
    fi
    set -e

    v_echo "Getting data"

    RECEIVED_DATA=$(cat /tmp/serial_received_data)
    rm /tmp/serial_received_data

    v_echo "SEND_DATA=$SEND_DATA"
    v_echo "RECIEVED_DATA=$RECEIVED_DATA"

    # now actually compare the data to get the testcase result
    if [ "$SEND_DATA" != "$RECEIVED_DATA" ] ; then
        echo "# sent data: $SEND_DATA"
        echo "# received data: $RECEIVED_DATA"
        fail "$TESTCASE" "Received data does not match sent data"
        return
    else
        succeed "$TESTCASE"
        return
    fi
}

for BAUD_RATE in "$@" ; do
    test_one_rate
done

echo "Done."
