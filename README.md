LabControl is a system for controling elements of an autmoated testing lab.
This is sometimes referred to a board farm.

LabControl consists of two main elements:
 - the LabControl server, which stores items that represent the lab
    resources and 

json and yaml test object web server.

Introduction
============
LabControl server handles the user interface (HTML),
as well as web-based object storage for 
the following Fuego objects
 * Boards
 * Resources

In the future, additional objects may be stored, including:
 * user objects

Starting in foreground mode
===========================
To start fserver in foreground mode, cd to the top-level directory,
and run the script: start_server.

You may specify a TCP/IP port address for the server to use, on the
command line, like so:
 $ start_server 8001

By default, port 8000 is used.

In foreground mode, the program runs directly in the terminal where
fserver was started, and log messages are displayed on the screen
as the server processes network requests.

To stop the server, use CTRL-C (possibly a few times), to interrupt
the running server.

To start fserver in background mode, use the script: start_local_bg_server.
You may specify the TCP/IP port address fro the server to use, on the
command line, like so:
 $ start_local_bg_server [<port>]

In this case, the log data from the server will be placed in the
file: /tmp/test-server-log.output

To stop the server, use the following command:
  $ kill $(pgrep -f test-server)

Accessing the server
====================
To access the server using a web browser, go to:
 http://<ip address>:<port>/lcserver.py

To access the server using the command line, use:
 * lc 
 * ftc list-requests
 * ftc run-request
 * ftc put-run
 * ftc put-binary-package
 * ftc list-boards -r

Configuring 'lc' to access the server
======================================
To access the server using the lc command, you need to configure
it with the address of the server.

Put the following lines in the /etc/labcontrol.conf

server=localhost:8091/

If using a remote labcontrol service, use one of the following configurations:
server=<domain>/cgi-bin/lc
server=<domain>/labcontrol/