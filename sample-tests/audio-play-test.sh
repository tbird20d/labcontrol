#!/bin/sh
#
# audio-play-test.sh - use lc to do an audio play test
#
# This is a very simple test, sending data from the DUT audio output
# to an audio input resource in the lab
#
# outline:
#  discover lab endpoint for DUT audio connection
#  # set configuration of DUT audio device
#  # set configuration of lab audio receiver endpoint
#  start capture for lab endpoint (microphone input)
#  write data to DUT audio device (ie play something)
#  get data from lab capture
#  compare data - test success or failure (FIXTHIS - needs work)
#  delete lab capture
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
Usage: audio-play-test.sh [-h] [-v] <board> <device_id> <device> <audio-file>

Play data to the audio output of a board, and receive it in the lab.
Arguments:
  <board>      Board name registered with lab server
  <device_id>  String identifier for audio output under test (e.g. usb-audio)
               This is used to find the lab endpoint for the audio connection.
  <device>     Audio device for output under test (e.g. 0)
  <audio-file> A file to play for the test

Options:
 -h, --help  Show this usage help
 -v          Show verbose output

Test output is in TAP format.  This test requires that a board farm
REST API client be installed on the board (e.g. 'lc' or 'ebf'), and that
the server configuration has previously been set up -- that is, the
client is logged in, the indicated board is reserved, audio output hardware
is set up, and the correct configuration for the audio receiver resource
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
    error_out "Missing board argument for audio test"
fi
shift

DEVICE_ID="$1"
if [ -z $DEVICE_ID ] ; then
    error_out "Missing device argument for audio test"
fi
shift

DEVICE="$1"
if [ -z $DEVICE ] ; then
    error_out "Missing device argument for audio test"
fi
shift

AUDIO_FILE="$1"
if [ -z $AUDIO_FILE ] ; then
    error_out "Missing audio file for audio test"
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

RESOURCE=$($CLIENT $BOARD get-resource audio $DEVICE_ID)
if [ "$?" != "0" ] ; then
    error_out "Could not get resource for $BOARD:$DEVICE_ID"
fi

echo "Starting audio playback test"
v_echo "RESOURCE=$RESOURCE"

base_filename="$(basename $AUDIO_FILE)"
echo "Uploading audio file $base_filename to board $BOARD"
TMP_AUDIO_FILE="/tmp/$base_filename"
$CLIENT $BOARD upload $AUDIO_FILE $TMP_AUDIO_FILE

test_one_case() {
    TESTCASE="Check playing sample $base_filename"

    #v_echo "Configuring for sample rate $SAMPLE_RATE"

    # Configure sample rate for the DUT and lab endpoint
    # FIXTHIS - does this make sense, or should the receiver always be at the
    # same (high) sample rate?

    #echo "{ \"rate\": \"$SAMPLE_RATE\" }" | \
    #    $CLIENT $RESOURCE set-config audio
    #if [ "$?" != "0" ] ; then
    #    fail "$TESTCASE" "Could not set sample rate $SAMPLE_RATE for $RESOURCE"
    #    return
    #fi

    v_echo "Capturing data at lab resource"
    TOKEN="$($CLIENT $RESOURCE audio start)"
    if [ "$?" != "0" ] ; then
        fail "$TESTCASE" "Could not start audio capture with $RESOURCE"
        return
    fi

    # use for debugging
    #echo "capture token=$TOKEN"

    # give time for receiver to fully settle - I'm not sure this is needed
    sleep 0.5

    v_echo "Playing audio file from DUT"
    $CLIENT $BOARD run "aplay -d $DEVICE $TMP_AUDIO_FILE"

    v_echo "Stopping audio capture"

    $CLIENT $RESOURCE audio stop $TOKEN
    if [ "$?" != "0" ] ; then
        fail "$TESTCASE" "Could not stop audio capture"
        return
    fi

    v_echo "Getting data"

    $CLIENT $RESOURCE audio get-data $TOKEN -o /tmp/received-audio.au
    if [ "$?" != "0" ] ; then
        fail "$TESTCASE" "Could not get audio data"
        return
    fi

    v_echo "Deleting the data on the server"

    #$CLIENT $RESOURCE audio delete $TOKEN" || \
    #    echo "Warning: Could not delete data on server"
    echo "not doing: $CLIENT $RESOURCE audio delete $TOKEN" || \
        echo "Warning: Could not delete data on server"

    v_echo "PLAYED_DATA=$AUDIO_FILE"
    v_echo "RECIEVED_DATA=$/tmp/received-audio.au"

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

test_one_case

echo "Done."
