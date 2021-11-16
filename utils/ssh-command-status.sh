# It runs a command on target board to check command execution status of
# the target board.
#
# Usage: command_status.sh [-h|--help] -u {user_name} -p {password} -a    {ipaddr} -c {command}
#
# NOTES: if you get an error message:
#    "Could not create directory '/var/www/.ssh'"
# Do the following as root:
#   mkdir -p /var/www/.ssh ; \
#     chown www-data.www-data /var/www/.ssh \
#     chmod 700 /var/www/.ssh
#

usage() {
    cat <<HERE
Usage: command_status.sh [options] -u {user_name} -p {password} -a {ipaddr}

It runs a command on target board to check command execution status
of the target board.  The first line of output will be the single
word "OPERATIVE" or "INOPERATIVE", depending on whether the
command could be run on the board.  Use '-v' to see verbose execution
and error reporting information, in case of problems.

Options:
 -h|--help          Show usage help
 -v                 Show verbose output
 -u {user_name}     Specify the user name of target board
 -p {password}      Specify the password of target board.
 -a {ipaddr}        Specify the ip address or host name of the target board
 -c {command}       Specify the command to be executed on the target board.
                    Defaults to 'true' if not specified.
 -t {timeout}       Specify the ssh ConnectTimeout in seconds (default=2)

NOTE: if you get the error message:
   "Could not create directory '/var/www/.ssh'"
you can fix it by doing the following as root:
   cd /var/www ; mkdir -p .ssh ; chown www-data.www-data .ssh ; chmod 700 .ssh

HERE
    exit 1
}
verbose=false
CMD="true"
TIMEOUT=2
while [ -n "$1" ] ; do
    #echo "$1"
    case $1 in
        -h|--help)
            usage
            ;;
        -u) UNAME=$2
            shift 2
            ;;
        -p)
            PASSWORD=$2
            shift 2
            ;;
        -a)
            IPADDR=$2
            shift 2
            ;;
        -c)
            CMD=$2
            shift 2
            ;;
        -t)
            TIMEOUT=$2
            shift 2
            ;;
	    -v)
	        verbose=true
	        shift 1
	        ;;
        *)
            echo "Error: Unknown option '$1'"
            echo "Use '-h' to get program usage help"
            exit 1
            ;;
    esac
done

if [ -z "$IPADDR" ] ; then
    echo "Error: Missing host IP address or hostname.  Cannot continue."
    echo "Use '-h' for usage help"
    exit 1
fi

if [ -z "$UNAME" ] ; then
    echo "Error: Missing user name.  Cannot continue."
    echo "Use '-h' for usage help"
    exit 1
fi

SSH_ARGS="-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=$TIMEOUT"

stderr_outfile=$(mktemp)
if [ -n "$PASSWORD" ] ; then
    SS_CMD="sshpass -p $PASSWORD ssh $SSH_ARGS $UNAME@$IPADDR $CMD 2>$stderr_outfile"
else
    SS_CMD="ssh $SSH_ARGS $UNAME@$IPADDR $CMD 2>$stderr_outfile"
fi
$CMD
status=$?

#echo "$verbose"

# first output the status
if [ "$status" = "0" ] ; then
	echo "OPERATIVE"
else
    echo "INOPERATIVE"
fi

# second, output more diagnosis, if requested
if [ "$verbose" = "true" ] ; then
    echo -n "  "
    if [ $status -eq 0 ] ; then
        echo "Command was succesful"
    elif [ $status -eq 255 ] ; then
        echo "Exit status out of range"
    elif [ $status -eq 126 ] ; then
        echo "Command invoked cannot execute"
    elif [ $status -eq 127 ] ; then
        echo "Command not found"
    elif [ $status -eq 128 ] ; then
        echo "Invalid argument to exit command"
    elif [ $status -eq 130 ] ; then
        echo "Bash script terminated by Control-C -->Script terminated"
    else
        echo "Command failed due to unknown reason"
    fi
    echo "  Executed command: $SS_CMD"
    echo "  Command exit code=$status"
    if [ -s "$stderr_outfile" ] ; then
        echo -n "  STDERR from command: "
        cat $stderr_outfile
        echo
    fi
fi

# remove stderr data temp file
if [ -f "$stderr_outfile" ] ; then
    rm $stderr_outfile
fi
