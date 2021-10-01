#!/bin/sh
# Get an video from a webcam attached to a board in the lab
# using ttc and the 'ffmpeg' program.
#
# usage: ttc-get-ffmpeg-video.sh -t target -d duration -o output_file
#

set -x

usage() {
    cat <<HERE
Usage: ttc-get-ffmpeg-duration.sh [-h|--help] -t {target_board} -d {duration} -o {output_file}

Record a video using a camera on a target board, using the 'ffmpeg'
app, and transfer it back to the host into the indicated
output file.

Options:
 -h|--help          Show usage help
 -t {target_board}  Specify the target board to take the picture
 -d {duration}      Specify the duration (in seconds) for the video recording.
 -o {output_file}   Specify the filename to store the image into.
HERE
    exit 1
}

while [ -n "$1" ] ; do
    case $1 in
        -h|--help)
            usage
            ;;
        -t)
            TARGET=$2
            shift 2
            ;;
        -d)
            DURATION=$2
            shift 2
            ;;
        -o)
            OUTPUT_FILE=$2
            shift 2
            ;;
        *)
            echo "Error: Unknown option '$1'"
            echo "Use '-h' to get program usage help"
            exit 1
            ;;
    esac
done

if [ -z "$TARGET" ] ; then
    echo "Error: Missing target board"
    echo "Use '-h' to get program usage help"
    exit 1
fi

if [ -z "$DURATION" ] ; then
    echo "Error: Missing duration for video recording"
    echo "Use '-h' to get program usage help"
    exit 1
fi

if [ -z "$OUTPUT_FILE" ] ; then
    echo "Error: Missing output file"
    echo "Use '-h' to get program usage help"
    exit 1
fi

TARGET_FILENAME=/tmp/ffmpeg-video-$RANDOM.mp4

term_handler() {
    # terminate ffmpeg on the target, and download partial data
    ttc $TARGET run "killall ffmpeg"

    # wait for ffmpeg to finish writing file
    sleep 2

    get_video_file
    exit 0
}

get_video_file() {
    echo "Downloading video recording"
    ttc $TARGET cp target:$TARGET_FILENAME $OUTPUT_FILE
    echo "result=$?"
    echo "Removing image"
    ttc $TARGET run rm $TARGET_FILENAME
    echo "Image is at: $OUTPUT_FILE"
    FILENAME=$(basename $OUTPUT_FILE)
}

trap term_handler TERM

# FIXTHIS - should handle more ffmpeg options here
echo "Doing video recording to $TARGET:$TARGET_FILENAME"
ttc $TARGET run ffmpeg -t $DURATION -i /dev/video0 $TARGET_FILENAME
ffmpeg_rcode="$?"
echo "result=$ffmpeg_rcode"
get_video_file
exit

