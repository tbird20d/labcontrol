#!/bin/sh
#
# power-measurement-test.sh - use lc to perform power measurement tests
#
# This is a very simple test, measuring power usage of the DUT while doing
# a find operation at the root.
#

start_time=$(date +"%s.%N")

CLIENT="lc"

error_out() {
    echo "Error: $1"
    exit 1
}

if [ -z $1 ] ; then
    echo "Missing board argument for power test"
    exit 1
fi

if [ "$1" = "-h" -o "$1" = "--help" ] ; then
    echo "Usage: power-measurement-test.sh <board>"
    exit 0
fi

BOARD="$1"

echo "Using client $CLIENT with board $BOARD"

echo "Getting power measurement resource for board"

RESOURCE=$($CLIENT $BOARD get-resource power-measurement)
if [ "$?" != "0" ] ; then
    error_out "Could not get power measurement resource"
fi

echo "RESOURCE=$RESOURCE"

echo "Starting power measurement"

token=$($CLIENT $RESOURCE power-measurement start)
if [ "$?" != "0" ] ; then
    error_out "Could not start power measurement with $CLIENT, with resource $RESOURCE"
fi

echo "token=$token"

echo "Performing some workload (find)"

# run the test no longer than 1 minute
timeout -k 5s 20s sudo find / 2>&1 >/dev/null

echo "Stopping power measurement"

$CLIENT $RESOURCE power-measurement stop $token || \
    error_out "Could not stop power measurement with $CLIENT"

echo "Getting data"

echo "Here is the data:"
$CLIENT $RESOURCE power-measurement get-data $token || \
    error_out "Could not get power data with $CLIENT, using token $token"

echo "Deleting the data on the server"

$CLIENT $RESOURCE power-measurement delete $token || \
    echo "Warning: Could not delete data for token $token on server"

echo "FIXTHIS - should analyze the data here..."
echo "Done."
