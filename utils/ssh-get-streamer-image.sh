#!/bin/sh
# Get an image from a webcam attached to a board in the lab
# using ssh (and sshpass) and the 'streamer' program.
#
# usage: ssh-get-streamer-image.sh -u -p -a -o output_file
#

usage() {
    cat <<HERE
Usage: ssh-get-streamer-image.sh [-h|--help] [options] {host} -o {output_file}

Take a picture on a host, using the 'streamer'
app, and transfer it back to the host into the indicated
output file.

Options:
 -h|--help          Show usage help
 {host}             Specify the network address or hostname of the machine
                    where the image will be captured.
 -u {user}          Specify the user account for the operation
 -p {password}      Specify the password for the operation
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
            USER="${2}@"
            shift 2
            ;;
        -p)
            SSHPASS_CMD="sshpass -p $2 "
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

if [ -z "$OUTPUT_FILE" ] ; then
    echo "Error: Missing output file"
    echo "Use '-h' to get program usage help"
    exit 1
fi


HOST_FILENAME=/tmp/streamer-image-$RANDOM.jpeg

# FIXTHIS - should handle more streamer options here

echo "Doing image capture to $HOST:$HOST_FILENAME"
${SSHPASS_CMD} ssh ${USER}@${HOST} streamer -f jpeg -s 640x480 -o $HOST_FILENAME
echo "result=$?"
echo "Downloading image"
${SSHPASS_CMD} scp ${USER}@${HOST}:$HOST_FILENAME $OUTPUT_FILE
echo "result=$?"
echo "Removing image"
${SSHPASS_CMD} ssh ${USER}@${HOST} rm $HOST_FILENAME
echo "Image is at: $OUTPUT_FILE"
FILENAME=$(basename $OUTPUT_FILE)
