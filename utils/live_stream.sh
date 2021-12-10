#!/bin/sh
INPUTFILE=input_uvc.so
OUTPUT_FILE=output_http.so
CHANNEL=/dev/video0
mjpg_streamer -i "$INPUTFILE -hf true -vf true  -d $CHANNEL " -o "$OUTPUT_FILE -p 8085 -w /var/www/stream.html" >> /dev/null 2>&1 &
