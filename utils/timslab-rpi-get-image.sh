#!/bin/sh
# get an image from a webcam attached to a board in the lab
#
# usage: get-image.sh -t target -o output_file
#

usage() {
    cat <<HERE
Usage: timslab-get-image.sh [-h|--help] -t {target_board} -o {output_file}

Take a picture on a target board, using the 'streamer'
app, and transfer it back to the host into the indicated
output file.

Options:
 -h|--help          Show usage help
 -t {target_board}  Specify the target board to take the picture
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

if [ -z "$OUTPUT_FILE" ] ; then
    echo "Error: Missing output file"
    echo "Use '-h' to get program usage help"
    exit 1
fi

TARGET_FILENAME=/tmp/streamer-image.jpeg

# FIXTHIS - should handle more streamer options here
ttc $TARGET run streamer -f jpeg -s 640x480 -o $TARGET_FILENAME
ttc $TARGET cp target:$TARGET_FILENAME $OUTPUT_FILE
ttc $TARGET run rm $TARGET_FILENAME
