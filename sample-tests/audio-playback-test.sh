#!/bin/sh
#
# audio-play-test.sh - use lc to do an audio play test
#
# This is a very simple test, sending data from the DUT audio output
# to an audio input resource in the lab
#
# outline:
#  discover lab endpoint for DUT audio connection
#  - specify configuration of DUT audio device
#  start capture for lab endpoint (microphone input)
#  write data to DUT audio device (ie play something)
#  get data from lab capture
#    trim start and end of captured data
#  compare data - test success or failure (FIXTHIS - needs work)
#  delete lab capture and temp files
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
Usage: audio-play-test.sh [-h] [-v] <board> <device> <device_id> <audio-file>

Play data to the audio output of a board, and receive it in the lab.
Arguments:
  <board>      Board name registered with lab server
  <device>     Audio device for output under test (e.g. 'default', 'plughw:...')
  <device_id>  String identifier for audio output under test (e.g. usb-audio)
               This is used to find the lab endpoint for the audio connection.
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

DEVICE="$1"
if [ -z $DEVICE ] ; then
    error_out "Missing device argument for audio test"
fi
shift

DEVICE_ID="$1"
if [ -z $DEVICE_ID ] ; then
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
echo "1..3"

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
DUT_TMP_AUDIO_FILE="/tmp/$base_filename"
CAPTURED_AUDIO_FILE="/tmp/captured-audio.wav"

$CLIENT $BOARD upload $AUDIO_FILE $DUT_TMP_AUDIO_FILE

# uses the following variables:
# CLIENT, TESTCASE, BOARD, DEVICE, DEVICE_ID, SAMPLE_RATE
test_one_case() {
    v_echo "==== Doing audio playback actions on $BOARD ===="
    TESTCASE="Audio playback actions with $base_filename"

    #v_echo "Configuring for sample rate $SAMPLE_RATE"

    # NOTE: the receiver is always at a high sample rate
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
    sleep 0.3

    # Configure sample rate for the DUT endpoint

    v_echo "Playing audio file from DUT"

    #if [ -z "$SAMPLE_RATE" ] ; then
    #    SAMPLE_RATE=44100
    #fi

    # generate a sample file for playback on the DUT

    $CLIENT $BOARD run "aplay -D $DEVICE $DUT_TMP_AUDIO_FILE"

    v_echo "Stopping audio capture"

    $CLIENT $RESOURCE audio stop $TOKEN
    if [ "$?" != "0" ] ; then
        fail "$TESTCASE" "Could not stop audio capture"
        return
    fi

    v_echo "Getting data"

    # get-data is not good for arbitrary binary data, use get-ref instead,
    # followed by a download
    #echo $CLIENT $RESOURCE audio get-ref $TOKEN
    DATA_REF=$($CLIENT $RESOURCE audio get-ref $TOKEN)
    if [ "$?" != "0" ] ; then
        fail "$TESTCASE" "Could not get reference to audio data"
        return
    fi

    echo curl -s $DATA_REF -o $CAPTURED_AUDIO_FILE
    curl -s $DATA_REF -o $CAPTURED_AUDIO_FILE

    v_echo "Deleting the data on the server"

    $CLIENT $RESOURCE audio delete $TOKEN || \
        echo "Warning: Could not delete data on server"

    v_echo "AUDIO_FILE -Played- =$AUDIO_FILE"
    v_echo "AUDIO FILE -Captured- =$CAPTURED_AUDIO_FILE"

    succeed $TESTCASE

    # Trim the captured file: 1.5 seconds at start and end
    sox $CAPTURED_AUDIO_FILE /tmp/temp1.wav trim 1.5
    sox /tmp/temp1.wav /tmp/temp2.wav reverse trim 1.5 reverse

    # now actually compare the data to get the testcase result
    # put alsabat or sox comparison in here
    ALSABAT=/home/tbird/work/audio-stuff/alsa-utils/bat/alsabat

    v_echo "==== Doing ALSABAT analysis (tone checking) ===="

    # compare audio at same frequency, and see if it passes the test
    $ALSABAT --readcapture=/tmp/temp2.wav | tee /tmp/audio-test-results.txt

    TESTCASE="Alsabat tone check with $base_filename"

    # look at the results and see what needs to be compared
    if grep PASS /tmp/audio-test-results.txt ; then
        succeed "$TESTCASE"
    else
        fail "$TESTCASE"
    fi

    v_echo "==== Doing SOX analysis ===="

    TESTCASE="SOX frequency check with $base_filename"

    sox $AUDIO_FILE -n stat 2>&1 | tee /tmp/sox-stats-played.txt
    sox /tmp/temp2.wav -n stat 2>&1 | tee /tmp/sox-stats-captured.txt

    PLAYED_FREQ=$(grep frequency /tmp/sox-stats-played.txt | sed s/.*://)
    CAPTURED_FREQ=$(grep frequency /tmp/sox-stats-captured.txt | sed s/.*://)

    echo "PLAYED_FREQ=$PLAYED_FREQ"
    echo "CAPTURED_FREQ=$CAPTURED_FREQ"

    DELTA_THRESHOLD=20
    DELTA=$(( PLAYED_FREQ - CAPTURED_FREQ ))
    DELTA=${DELTA#-}

    echo "Frequency Delta=$DELTA"
    if [ $DELTA -gt $DELTA_THRESHOLD ] ; then
        fail "$TESTCASE"
    else
        succeed "$TESTCASE"
    fi

    v_echo "Cleaning up"
    v_echo "Removing captured audio file: $CAPTURED_AUDIO_FILE"
    rm $CAPTURED_AUDIO_FILE
    rm /tmp/temp1.wav /tmp/temp2.wav /tmp/audio-test-results.txt
    rm /tmp/sox-stats-played.txt /tmp/sox-stats-captured.txt
}

test_one_case

echo "Done."
