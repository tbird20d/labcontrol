#!/bin/bash
#
# start mjpg-streamer on /dev/video0
#
# This is very finicky.  Resolution must be exactly as shown, or
# it will fail silently.  (You'd think that 640x480 would work,
# but you'd be wrong.)
#
# This expects to be run as user 'lc'
# the mjpg-streamer app and plugins need to be located at the
# base directory (and compiled and ready to run)
#
# See https://github.com/jacksonliam/mjpg-streamer.git for this code
#

base_dir=/home/lc/work/mjpg-streamer/mjpg-streamer-experimental
log=/home/lc/work/mjpg-streamer/streamer.log

export LD_LIBRARY_PATH=.
cd $base_dir
nohup ./mjpg_streamer \
    -i "input_uvc.so -d /dev/video0 -r 960x540" \
    -o "output_http.so -w ./www" \
    &>$log &
echo "Streaming started - see log at $log"
