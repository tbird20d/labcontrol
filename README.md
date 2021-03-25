LabControl is a system for controling elements of an automated testing lab.
This is sometimes referred to a board farm.

LabControl consists of two main elements:
 - the LabControl server, which stores items that represent the lab
   boards and resources
 - the labcontrol client, which can add items to the server, and
   issue commands to resources in the lab

Introduction
============
LabControl server handles the user interface (HTML),
as well as web-based object storage for the following lab objects
 * Boards
 * Resources

In the future, additional objects may be stored, including:
 * User objects

Starting in foreground mode
===========================
To start lcserver in foreground mode, cd to the top-level directory,
and run the script: start_server.

You may specify a TCP/IP port address for the server to use, on the
command line, like so:

```
 $ start_server 8001
```

By default, port 8000 is used.

In foreground mode, the program runs directly in the terminal where
fserver was started, and log messages are displayed on the screen
as the server processes network requests.

To stop the server, use CTRL-C (possibly a few times), to interrupt
the running server.

To start lcserver in background mode, use the script: start_local_bg_server.
You may specify the TCP/IP port address fro the server to use, on the
command line, like so:
```
 $ start_local_bg_server [<port>]
```

In this case, the log data from the server will be placed in the
file: /tmp/test-server-log.output

To stop the server, use the following command:
```
  $ kill $(pgrep -f test-server)
```

Accessing the server
====================
To access the server using a web browser, go to:
 http://<ip address>:<port>/lcserver.py

To access the server using the command line, use the 'lc' command:
 * lc help - to get command line help
 * lc help <command> - to get help for an 'lc' command
 * lc list-boards - to list the boards managed by the server
 * lc list-resources - to list resources managed by the server
 * lc {board} power reboot
 * lc {board} power on
 * lc {board} power off
 * lc {board} power status

FIXTHIS - need to make these match the options used by 'ebf'

Configuring 'lc' to access the server
======================================
To access the server using the lc command, you need to configure
it with the address of the server.

Put the following lines in the /etc/lc.conf

```
server=localhost:8000
```

If using a remote labcontrol service, use one of the following configurations:
 * server={domain}/cgi-bin
 * server={domain}/labcontrol

depending on the URI path to the lcserver.py CGI script as it is
installed in the remote web server.
