#!/bin/sh
# Get an video from a webcam attached to a board in the lab
# using ssh and the 'ffmpeg' program.
#
# usage: ssh-get-ffmpeg-video.sh -t target -d duration -o output_file
#

set -x

usage() {
    cat <<HERE
Usage: ssh-get-ffmpeg-duration.sh [-h|--help] -t {target_board} -d {duration} -o {output_file}

Record a video using a camera on a remote machine, using the 'ffmpeg'
app, and transfer it back to the host into the indicated
output file.

Options:
 -h|--help          Show usage help
 {host}             Specify the network address or hostname of the machine
                    where the image will be captured.
 -u {user}          Specify the user account for the operation
 -p {password}      Specify the password for the operation
 -d {duration}      Specify the duration (in seconds) for the video recording.
 -o {output_file}   Specify the filename to store the image into.
HERE
    exit 1
}

SSHPASS_CMD=""
while [ -n "$1" ] ; do
    case $1 in
        -h|--help)
            usage
            ;;
        -u)
            USER=$2
            shift 2
            ;;
        -p)
            SSHPASS_CMD="sshpass -p $2 "
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
            if [ -z "${HOST}" ] ; then
                HOST="$1"
                shift
            else
                echo "Error: Unknown option '$1'"
                echo "Use '-h' to get program usage help"
                exit 1
            fi
            ;;
    esac
done

if [ -z "$HOST" ] ; then
    echo "Error: Missing host for operation"
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

HOST_FILENAME=/tmp/ffmpeg-video-$RANDOM.mp4

term_handler() {
    # terminate ffmpeg on the target, and download partial data
    ${SSHPASS_CMD} ssh ${USER}@${HOST} "killall -s 15 ffmpeg"

    # wait for ffmpeg to finish writing file
    sleep 2

    get_video_file
    exit 0
}

get_video_file() {
    echo "Downloading video recording"
    ${SSHPASS_CMD} scp ${USER}@${HOST}:$TARGET_FILENAME $OUTPUT_FILE
    echo "result=$?"
    echo "Removing image"
    ${SSHPASS_CMD} ssh ${USER}@$HOST} rm $TARGET_FILENAME
    echo "Image is at: $OUTPUT_FILE"
    FILENAME=$(basename $OUTPUT_FILE)
}

trap term_handler TERM

# FIXTHIS - should handle more ffmpeg options here
echo "Doing video recording to $TARGET:$TARGET_FILENAME"
${SSHPASS_CMD} ssh ${USER}@${HOST} ffmpeg -t $DURATION -i /dev/video0 $TARGET_FILENAME
ffmpeg_rcode="$?"
echo "result=$ffmpeg_rcode"
get_video_file
exit

