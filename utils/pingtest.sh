# It pings the target board to check network status of the target board.
# Usage: pingtest.sh [-h|--help] {ipaddr}

MAX_DURATION=1
INTERVAL=0.3
COUNT=3

usage() {
    cat <<HERE
Usage: pingtest.sh [options] {ipaddr}

Pings a network address (or hostname) to check the network
status of a board.  The default is 3 pings in 1 second.

Options:
 -h|--help          Show usage help
 -m {max_duration}  Wait no longer than max_duration seconds
 -c {count}         Send this many ping requests
HERE
    exit 1
}

unset VERBOSE
while [ -n "$1" ] ; do
     case $1 in
        -h|--help)
            usage
            ;;
        -m) MAX_DURATION=$2
            shift 2
            ;;
        -c) COUNT=$2
            shift 2
            ;;
        -*)
            echo "Error: Unknown option '$1'"
            echo "Use '-h' to get program usage help"
            exit 1
            ;;
        *)
            if [ -z "$IPADDR" ] ; then
                IPADDR=$1
                shift
            else
                echo "Error: Unknown option '$1', already using IP addr $IPADDR"
                echo "Use '-h' to get program usage help"
                exit 1
            fi
            ;;
    esac
done

if [ -z "$IPADDR" ] ; then
    echo "Error: Missing IP addr (or hostname) to ping"
    exit 1
fi

ping -q -w $MAX_DURATION -i $INTERVAL -c $COUNT $IPADDR > /dev/null && \
    echo "RESPONSIVE" || echo "NONRESPONSIVE"
