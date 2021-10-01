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
 * User objects

Dependencies
============

* LabControl server requires Python 3 and the 'psutil' module
  * Under Debian or Ubuntu, do: "apt install python3-psutil"
  * The test server requires the http.server module, but this is included
    in the core distribution of Python3
* The Labcontrol client ('lc') requires Python 3 and the 'requests' module
  * Under Debian or Ubuntu, do: "apt install python3-requests"


Configuration
=============

Before starting `lcserver` either create base directory in its default path
(`/usr/local/src/labcontrol/lc-data`) or set `base_dir` variable in the
`lcserver.conf`. Remeber to copy the configuration file to
`/etc/lcserver.conf`.

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
the server was started, and log messages are displayed on the screen
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
 http://{ip address}:{port}/lcserver.py

To access the server using the command line, use the 'lc' command:
 * lc help - to get command line help
 * lc help {command} - to get help for an 'lc' command
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

If you are using the simple test http server included with labcontrol,
put the following lines in the /etc/lc.conf

```
server=http://localhost:8000/lcserver.py
```

If using a labcontrol service running under a "real" web server,
use a configuration like the following:

 * server=https://mydomain.org/cgi-bin/lcserver.py

depending on the URI path to the lcserver.py CGI script as it is
installed in the remote web server.
