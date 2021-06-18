Introduction
============

Here is some documentation about the objects and schema used
by labcontrol.

Labcontrol objects are represented as json files in a data directory.

The three main object types supported by labcontrol are:
 * users
 * boards
 * resources

The labcontrol server provides the means to control objects in a 
board farm.  Specifically, it provides a web user interface, for manual
access and control of board farm (lab) objects, as well as REST APIs
for client-based access.  Included in the labcontrol system is 
both the server, and a REST API client, called 'lc'.

The general architecture of labcontrol is that a lab consists of
one or more boards, and resources that are associated with, or connected
to those boards.  Operations, such as turning power on or off to a 
board, can be accomplished by performing actions either directly to
a board, or by first getting the resource associated with that action for
that board, then requesting the resource to perform the action.


Users
=====

Users are used to provide security, so that only authenticated users
can perform actions on boards.  Some actions can be destructive
(such as overriting files), so restricting access to actions
to only allows users is important.

A user is defined by a file in lc-data/data/users that has the
following characteristics
 * name
 * password
 * auth_token

The name of the file is the user's name, prefixed with the string
"user-" and with the file extension ".json".

So, the the user file for a user with name "tbird", might look
like this:

Name of the user json file: user-tbird.json
Contents of the user json file:
{
     "name": "tbird",
     "password": "tims-pass123",
     "auth_token": "ef9382b66a18ec7f"
}

Currently, the password is stored in cleartext, but it would be
better to store it as a password hash (with a salt).

When a user "logs in" to the system, they provide their user account
name and password, and the system returns an authentication token.
That token is used in subsequent interactions with the server.
Each APi includes the api token.

Note that the API should only be conducted over secure http (https),
so that the security information is encrypted.  However, the test
servers used for development (e.g. start_server) only provide
access to the server over unencrypted HTTP.  It is envisioned
that a production installation of the server will be as a CGI script
to an https server.


Boards
======
A board is one of the primary objects that can be accessed and
cohtrolled by the labcontrol server.

A board is defined by a json file in the directory
lc-data/data/boards.

The filename of the board file is prefixed by "board-" and has the
extension ".json", with the middle of the filename being the board's
name.

So a board named "bbb" would have a board filename of "board-bbb.json"

Here are the fields that may be present in a board file:
 * AssignedTo
 * board
 * description
 * host
 * name
 * power_controller
 * power_measurement
 * run_cmd
 * serial_endpoints


AssignedTo indicates the user that this board is currently assigned
to.  This is set by doing a board reservation (aka an allocation).
The system is supposed to only allow that user to perform operations
on the board, but I'm not sure if this is implemented (in June 2021).

The board field is currently unused.

The description field has a description of the board.
The host field has the name of the lab that this board is in.

The name field has the name of the board.  This is the name that
will show up in a 'list devices' command, and will form part of
the urL for REST API calls.  The name may only consist of alphnumeric
characters (starting with a letter), and the '_' and '-' characters.
The name is case-sensitive.

power_controller is the name of the power controller resource
associated with this board

power_measurement is the name of one or more power measurement
resources associated with the board.
The value of power_measurement can either be a single string,
or a list of strings.

The 'run_cmd' is a program that will execute
a command on the board.  It customarily uses the value of the
'command' variable as part of the declaration of the command to
execute on the board.  [ See section xxx for information
on how commands are executed by the server. ]

The serial_endpoints field defines a list of resources that are
the lab endpoints for serial connections to the board.

Here is a sample definition for a board:
{
    "AssignedTo": "Tim",
    "board": "bbb",
    "description": "BeagleBone Black in Tim's lab",
    "host": "timslab",
    "name": "bbb",
    "power_controller": "sdb-042",
    "power_measurement": "sdb-042",
    "run_cmd": "ttc %(name)s run %(command)s",
    "serial_endpoints": ["serial-QB"]
}

NOTES: the 'board' field is superfluous, and should be removed.

Note that a board file may contain other fields for it's own
purposes.

Resources
=========

A resource is an item in a lab that is controls or is related to
some feature on a board.  For example a 'power_controller' resource
can be used to turn power off or on to a board.  A 'serial' resource
is used to represent the lab end of a connection to one of the serial
ports on a board.

A resource is defined with a json file, starting with the prefix
"resource-", followed by the resource name, and then ending
with the suffix ".json".  Resource files are located in the directory
lc-data/data/resources.

The fields in a resource file depend on its type.  The fields that
all resources have in common are:
 * name
 * board
 * host
 * description (optional)
 * type

The name is the name of the resource.  This is a string that starts
with a letter, and can consist of letters, numbers and the characters
"_" and "-".  This name is used as part of the url in the rest API to
access and operate the resource in the lab.

The 'board' field is the name of the board which this resource is
associated with.  The host field is the name of the lab where this
resource resides.  The optional description field has a description
of the resource.  This is intended to be used by a human lab technician to
distinguish this resource among other similar resources in the lab.
For example, it might have a serial number, or location, or describe some
identifying information about the hardware device that this resource
is associated with.

The 'type' field has a list of resource types for this resource.
A single hardware device in the lab may support multiple operations
(such as both power control and power measurement).

General note:

In the API and in the CLI, we use dashes between word elements for
things like the resource type (ie power-measurement).  However, in the
json files, we use underscores (power_measurement) in the field names,
so that the field can represent a python variable when the json is
loaded into the server.

Here is a sample resource file for a power controller and
power measurement device.  In this case, the name 'sdb' stands
for Sony Debug Board, but a resource name can be an arbitrary
string.

Power controller fields:
 * off_cmd
 * on_cmd
 * status_cmd
 * reboot_cmd

Each of these fields represents a command to run to perform
the indicated power operation on the board that this resource
is associated with.  The status_cmd field is a command that
should return "ON", "OFF", or "UNKNOWN" to indicate the
status of power being supplied to the board. (By 'return', we
mean that the command will print that single word on standard
output, when run.)

[[NOTE - document how to configure a single power controller
that controls multiple boards.  Can do it two ways:

1) with a single power controller and a "power_port" obtained from the
board definition (e.g resource 'pdu' and 'power_port' of '1' in one
board file, '2' in another board file, etc.)

2)  with separate power-controller resource definitions:
eg. pdu-1, pdu-2, pdu-3
]]

Power_measurement fields:
 * capture_cmd

The capture_cmd field is invoked when a labcontrol user initiates
a power_measurement 'start-capture' operation on this resource.  The
labcontrol server will prepare a filename to use for the command
to put the captured data into, and provide it as the variable
'logfile' to the command.

When the command is executed, the server records its process id.
When the user issues a 'stop capture' operation, the PID is used
to stop the running process using a SIGTERM signal.

The command should be capable of handling the SIGTERM signal, to flush
any outstanding buffered data, and close the capture file.

Serial endpoint fields
 * baud_rate
 * board_feature
 * capture_cmd
 * config_cmd
 * put_cmd
 * status_cmd

Serial resources have the same fields of name, type, board, host,
and description as other resources.

The baud_rate field indicates the current setting for the
baud rate of the serial endpoint hardware.  This is set
before a call to the program specified by the config_cmd.

It is anticipated that additional configuration settings will be
supported in the future (such as stop bits, parity, and flow
control settings).

The board_feature field indicates the name or identifier for
the board endpoint associated with the serial connection managed
by this resource.  A serial resource identifies one endpoint of
the serial connection, and the 'board_feature' attribute identifies
the other endpoint.  This can be a name, like 'uart1', or it can
be a device path on the board, such as /dev/ttyS1.  This is used when
a test is trying to find the associated lab endpoint for a particular
serial device on the board.

The config_cmd is a program to run to set the configuration of the
serial device in the lab associated with this resource.  Specifically,
it is the command to configure the lab endpoint (not the board
endpoint) of the serial connection.  As of June 2021, this command
can use the "baud_rate" variable as part of the command.
[[See section xxxx for how the variables are set before executing
the command]]

Section xxxx [[started]]
Before calling a command, the server will replace variables in the
value string for the command, with those that match from the
board or resource definition.  In some cases, special variables
are also set.

The convention is that commands are expressed in python2
named-variable syntax for python formatted strings.
The variables from the resource or board are put into a python
dictionary, then any special variables are added (such as 'logfile',
or 'baud_rate'), and then the string is interpolated using that
dictionary.  Python named-variable syntax uses '%' followed
by the variable name enclosed in parens, followed by 's', like
this: %(variable_name)s.

It is a common error to forget the trailing 's'.  If you
get a string formatting exception, when trying to execute
a command, this is the most likely cause.

For example, in the string: 
 "stty -F %(serial_dev)s %(baud_rate)s raw -echo -echoe -echok"

The 'serial_dev' variable is an arbitrary helper variable defined
in the resource file.  And the 'baud_rate' variable is a special
variable set by the server prior to executing a config_cmd string.

NOTE: one side effect of this interpolation is that any single
percent signs in command strings must be escaped, with another
percent sign.  e.g. if the field 'var1' had the value 'value1',
then the command string "foo %% %(var1)s' would be converted
into a final command string of 'foo % value1'

NOTE: the 'resource' field is not used and should be removed.

Here is a sample resource file for a serial port on the lab host
(where the labcontrol server is running), which serial port is
connected to uart1 on the board under test.

The filename is resource-serial-QB.json
The file contents are:
{
    "baud_rate": "921600",
    "board": "bbb",
    "board_feature": "uart1",
    "capture_cmd": "grabserial -d %(serial_dev)s -b %(baud_rate)s -Q -o %(logfile)s",
    "config_cmd": "stty -F %(serial_dev)s %(baud_rate)s raw -echo -echoe -echok",
    "host": "timslab",
    "name": "serial-QB",
    "put_cmd": "cat %(datafile)s >%(serial_dev)s",
    "resource": "serial-QB",
    "serial_dev": "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_AQ017RQB-if00-port0",
    "status_cmd": "stty -F %(serial_dev)s",
    "type": [
        "serial"
    ]
}


To Do
=====

Things to document:
 - directory layout
   -lc-data/data, files, pages
 - user authentication
 - object types
   - users, boards, resources
 - object schemas
    - user schema
    - board schema
    - resource schema
      - resource types
        - power-controller, power-measurement, serial
        - power-controller schema
        - power-measurement schema
        - serial schema
    - method of invocation for actions
      - power control
      - staring, stopping, getting a capture
      - configuring a resource
 - apis
 - web interface
 - sample operations
   - power on, off, reboot
   - power measurement
   - serial port control
   - audio capture
