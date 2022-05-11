#!/bin/bash
# alsa-record.sh - use arecord to start recording
#  this wrapper accomplishes a few different things
#   1) suid to a different account, so I have sufficient permissions to
#      access the audio devices under Linux (the web server user account
#      does not have sufficient permissions to do this).
#      (if there's a desktop running on the machine where arecord will
#      be run, then it might have the microphone reserved)
#   2) save a pid for the arecord session, so that it can be stopped
#      when this wrapper receives SIGTERM
#   3) suppress stderr output, when the command is successful
#      The labcontrol server looks at stderr, and if there's anything
#      in it, assumes the command had a problem.  However, arecord emits
#      messages to stderr even on success.

# never allow more than 30 minutes recording for a test
# (Should document this somewhere).  This is a failsafe in case
# the signal and pid handling gets messed up.  (I once had an arecord
# running for over a month on my desktop machine.)
MAX_DURATION=1800

# FIXTHIS - add MAX_DURATION handling

# trap SIGTERM
term_handler() {
    if [ -n "$ARECORD_PID" ] ; then
        kill -s SIGTERM $ARECORD_PID
        ARECORD_PID=''
    fi
    sleep 0.5
}

trap term_handler SIGTERM

STDERR_FILE=/tmp/arecord-$$.stderr

arecord "$@" 2>$STDERR_FILE &
ARECORD_PID="$!"

wait $ARECORD_PID
ARECORD_STATUS=$?

# if we see "Recording WAVE" in stderr, then assume success
# (we may need more stderr processing here, to detect errors
# after a recording starts)
if grep -q -s "Recording WAVE" $STDERR_FILE ; then
    # output to stdout (rather than stderr), lcserver ignores this
    cat $STDERR_FILE
else
    cat $STDERR_FILE >&2
fi
rm $STDERR_FILE
