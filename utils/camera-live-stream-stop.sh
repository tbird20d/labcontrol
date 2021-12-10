#!/bin/sh
#
# stop mjpeg-streamer on the current machine
# will be run as user 'lc'

# -15 is SIGSTOP; -9 also works, but may be a bit more drastic
# this assumes only one instance of mjpg_streamer is running
killall -15 mjpg_streamer
sleep .5
echo 'process terminated'
