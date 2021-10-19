# It runs a command on target board to check command execution status of
# the target board.
#
# Usage: command_status.sh [-h|--help] -u {user_name} -p {password} -a    {ipaddr} -c {command}


usage() {
    cat <<HERE
Usage: command_status.sh [-h|--help] -u {user_name} -p {password} -a {ipaddr} -c {command}

It runs a command on target board to check command execution status of the target board

Options:
 -h|--help          Show usage help
 -u {user_name}     Specify the user name of target board
 -p {password}      Specify the password of target board.
 -a {ipaddr}        Specify the ip address of the target board
 -c {command}       Specify the command to be executed on the target board.
 -t {timeout}       Specify the ssh ConnectTimeout (default=2)
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
        -u) USER=$2
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

SSH_ARGS="-o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=$TIMEOUT"

sshpass -p $PASSWORD ssh $SSH_ARGS $USER@$IPADDR $CMD 2>&1
status=$?

#echo "$verbose"

if [ "$verbose" = "true" ] ; then
	if [ $status -eq 255 ] ; then
		echo "Exit status out of range"
	elif [ $status -eq 126 ] ; then
		echo "Command invoked cannot execute "
	elif [ $status -eq 127 ] ; then
		echo "Command not found"
	elif [ $status -eq 128 ] ; then
		echo "Invalid argument to exit command "
	elif [ $status -eq 130 ] ; then
		echo "Bash script terminated by Control-C ----->Script terminated "
	elif [ $status -eq 0 ] ; then
		echo "OPERATIVE -----> command succesfull"

	else
		echo "NON-OPERATIVE ------> Command failed due to unknown reason"

	fi
else
	if [ "$status" = "0" ] ; then
		echo "OPERATIVE"

	else
		echo "INOPERATIVE"
	fi
fi
