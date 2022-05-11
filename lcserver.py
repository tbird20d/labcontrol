#!/usr/bin/python3
# vim: set ts=4 sw=4 et :
#
# lcserver.py - LabControl server CGI script
#
# Copyright 2020 Sony
#
# Implementation notes:
#  data directory = place where json object files are stored
#  files directory = place where file bundles are stored
#  pages directory = place where web page templates are stored
#
# The server implements three interfaces:
#  1. the human user interace (web pages showing objects and available
#  actions)
#  2. a human user interace showing raw object (files and json contents)
#  3. the computer ReST interface (used for sending, modifying and
#     retrieving the data in the store, and for performing REST API actions
#     on the objects)
#
# The server currently supports the top-level "pages":
#   boards, resources, requests, logs, users
#
# The REST API specified with Timesys uses urls like this:
#  /api/v0.2/devices/bbb/power/reboot
# I'm calling this the 'path api'
#
# To do:
# - convert everything over to the path api
#   + list devices
# - actions:
#   - upload - need to parse multi-part forms
# - queries:
#   - handle regex wildcards instead of just start/end wildcards
# - objects:
#   - support board registration - put-board
#   - support resource registration - put-resource
#   - support host registration
#   - support user registration
# - requests:
# - security:
#   - add otp authentication to all requests
#     - check host's otp file for specified key
#     - erase key after use
# - add hosts (or users)
#    - so we can: 1) save an otp file, 2) validate requests?
# - see also items marked with FIXTHIS
#

import sys
import os
import time
import cgi
import re
import tempfile
#import urllib
import urllib.parse
import uuid
import datetime

# simplejson loads faster than json, use that if available
try:
    import simplejson as json
except ImportError:
    import json

# import yaml as needed
#import yaml
import copy
import shlex
import subprocess
import signal
import threading   # used for Timer objects

debug = False
#debug = True

debug_api_response = False
# uncomment this to dump response data to the log file
#debug_api_response = True

# global used to store messages about reading config
config_msg = ""

# Keep track of which getstatusoutput I'm using
using_commands_gso = False
try:
    from subprocess import getstatusoutput
except:
    from commands import getstatusoutput
    using_commands_gso = True

VERSION=(0,6,5)


SERVER_CONF_FILENAME="/etc/lcserver.conf"

# stub class for storing incoming POST data of type application/json
# This works around a bug in the python cgi module handling this type of
# data.
# see https://github.com/webpy/webpy/issues/574#issuecomment-549070996
# and https://bugs.python.org/issue27777
# Use this instead of cgi.FieldStorage() class
# Right now, the only attributes used are form.value and form.getfirst()
class mycgiform_class:
    def __init__(self, data):
        self.value = data

    def getfirst(self, attr, default=None):
        if attr=="action":
            return "api"
        return default

# define a class for config vars
class config_class:
    def __init__(self):
        global config_msg

        # attempt to auto-detect several config values
        if os.path.exists("/usr/lib/cgi-bin/lcserver.py"):
            self.url_base = "/cgi-bin/lcserver.py"
        else:
            self.url_base = "/lcserver.py"

        self.url_prefix = "http://localhost/"
        self.files_url_base = "/lc-data"
        self.lab_name = "mylab"

        self.admin_contact_str = "&lt;Please set the admin_contact_str in lcserver.conf&gt;"

        # if not defined in lcserver.conf, try finding base_dir automatically
        # Precedence of installation locations:
        # 2. local lcserver in Fuego container
        # 3. test lcserver on Tim's private server machine (birdcloud.org)
        # 4. test lcserver on Tim's home desktop machine (timdesk)
        self.base_dir = "/home/ubuntu/work/labcontrol/lc-data"
        if not os.path.exists(self.base_dir):
            self.base_dir = "/usr/local/lib/labcontrol/lc-data"
        if not os.path.exists(self.base_dir):
            self.base_dir = "/home/tbird/work/labcontrol/lc-data"

        self.default_reservation_duration = "forever"
        self.default_video_recording_duration = "10"

        # #### this is the end of the defaults section ####
        # settings after this will not be overridden by the config file

        # allow items in the server config file to override default
        # or detected values
        #config_msg = "reading config file...<br>\n"
        if os.path.isfile(SERVER_CONF_FILENAME):
            data = open(SERVER_CONF_FILENAME, "r").read()
            for line in data.splitlines():
                #config_msg += "config line='%s'<br>\n" % line
                if not line.strip():
                    continue
                if line.startswith("#"):
                    continue
                if "=" in line:
                    name, value = line.split("=",1)
                    config_msg += "setting config %s='%s'<br>\n" % (name, value)
                    self.__dict__[name] = value

        self.data_dir = self.base_dir + "/data"
        self.files_dir = self.base_dir + "/files"
        self.page_dir = self.base_dir + "/pages"

        config_msg += "config='%s'" % self.__dict__

    def __getitem__(self, name):
        return self.__dict__[name]

# load the configuration data
config = config_class()

# if the file 'debug' exists in the lc-data directory, then
# turn on the debug flag (for extra logging)
if os.path.exists(config.base_dir + "/debug"):
    debug = True

RSLT_FAIL="fail"
RSLT_OK="success"

# this is used for debugging only
def log_this(msg):
    global config

    with open(config.base_dir+"/lcserver.log" ,"a") as f:
        f.write("[%s] %s\n" % (get_timestamp(), msg))

def dlog_this(msg):
    global debug
    global config

    if debug:
        with open(config.base_dir+"/lcserver.log" ,"a") as f:
            f.write("[%s] %s\n" % (get_timestamp(), msg))

# this class has data that can be included on a page
# using %(varname)s.  This includes things like login forms,
# search forms, menus, variable data, etc.
#
# items available to use are:
#   url_base, page_url, page_name, asctime, timestamp, version
#   version, git_commit, git_describe, body_attrs
#   login_link (and a bunch more)

class page_data_class:
    def __init__(self, req, init_data = {} ):
       self.data = init_data
       self.req = req
       self.cookies = ""

    def __getitem__(self, key):
        # return value for key
        # if the value is callable, return the string returned by calling it
        if key in self.data:
            item = self.data[key]
        elif hasattr(self, key):
            item = getattr(self, key)
        else:
            if "default" in self.data:
                item = self.data["default"]
            else:
                item = self.req.html_error('&lt;missing data value for key "%s"&gt' % key)
        if callable(item):
            return item()
        else:
            return item

    # this allows for getting arbitrary information for the system
    # using an external command
    # use with caution: try to prevent something like 'cat /etc/passwd'
    # !! never call this with user-provided data !!
    # this is for internal use only (e.g. see git_commit)
    def external_info(self, cmd, new_dir=None):
        saved_cur_dir = os.getcwd()
        try:
            if new_dir:
                os.chdir(new_dir)
            status, output = getstatusoutput(cmd)
            if status==0:
                output
            else:
                self.req.add_to_message("problem executing command: '%s'" % cmd)
                output = "#no data#"
        except:
            output = "#no data#"
            self.req.add_msg_and_traceback('exception in %s' % cmd)

        if new_dir:
            os.chdir(saved_cur_dir)
            return output

    def url_base(self):
        return self.req.config.url_base

    def files_url_base(self):
        return self.req.config.files_url_base

    def page_url(self):
        return self.req.page_url

    def page_name(self):
        return self.req.page_name

    def admin_page_link(self):
        if self.req.user.admin:
            return 'Click to go to the <a href="%s">Admin</a> page' % \
                    self.req.make_url("Admin")
        else:
            return ""

    def asctime(self):
        return time.asctime()

    def timestamp(self):
        return get_timestamp()

    def version(self):
        return "%d.%d.%d" % VERSION

    def git_commit(self):
        cmd = 'git log -n 1 --format="%h"'
        html = self.external_info(cmd, config.base_dir)
        return '#' + html.strip()

    def git_describe(self):
        cmd = 'git describe'
        html = self.external_info(cmd, config.base_dir)
        return html

    def user_name(self):
        return self.req.user.name

    def user_admin(self):
        return str(self.req.user.admin)

    def lab_name(self):
        return str(self.req.config.lab_name)

    def admin_contact_str(self):
        return str(self.req.config.admin_contact_str)

    # support edit action on a double-click on the page
    # FIXTHIS - the 'edit' action for a page is not currently supported
    def edit_on_dblclick(self):
        html = """ondblclick="location.href='%s?action=edit'" """ % self.req.page_url
        return ""
        return html

    def login_link(self):
        if self.req.user.name=="not-logged-in":
            html = """<a href="%s?action=login_form">Login</a>""" % (self.req.page_url)
        else:
            html =  """<a href="%s?action=edit_user_user_form">%s</a><br>
                       <a href="%s?action=logout">Logout</a>""" % \
            (self.req.page_url, self.req.user.name, self.req.page_url)
        return html

    def login_link_nobr(self):
        if self.req.user.name=="not-logged-in":
            return self.login_link()
        else:
            html =  """<a href="%s?action=edit_user_user_form">%s</a> <a href="%s?action=logout">Logout</a>""" % (self.req.page_url, self.req.user.name, self.req.page_url)
        return html

    def logout_link(self):
        return """<a href="%s?action=logout">Logout</a>""" % \
                (self.req.page_url)

    def search_form(self):
        html = """<FORM METHOD="POST" ACTION="%s?action=search">
    <table id=search_table><tr><td align=right>
    <INPUT type="text" name="search_string" width=15></input>
    </td></tr><tr><td align=right>
    <INPUT type="submit" name="search" value="Search"></input>
    </td></tr></table></FORM>
""" % self.req.page_url
        return html

    def search_form_nobr(self):
        html = """<FORM METHOD="POST" ACTION="%s?action=search">
    <INPUT type="text" name="search_string" width=15></input>
    <INPUT type="submit" name="search" value="Search"></input>
    </FORM>
""" % self.req.page_url
        return html

    def message(self):
        if self.req.message and not self.req.message_hold:
            html = """<table border=1 bgcolor=lightgreen width=100%%>
            <tr><td>%s</td></tr>
            </table>""" % self.req.message
            req.message = ""
        else:
            html = ""
        return html


class user_class:
    def __init__(self):
        self.name = "not-logged-in"
        self.admin = False

class req_class:
    def __init__(self, config, form):
        self.config = config
        self.data = page_data_class(self)
        self.header_shown = False
        self.footer_shown = False
        self.message = ""
        self.page_name = ""
        self.page_url = "page_name_not_set_error"
        self.form = form
        self.html = []
        self.api_path = ""
        self.obj_path = ""
        self.user = None

    def set_page_name(self, page_name):
        page_name = re.sub(" ","_",page_name)
        self.page_name = page_name
        self.page_url = self.make_url(page_name)

    def set_obj_type(self, page_path):
        page_path = re.sub(" ","_",page_path)
        if page_path and page_path[0] == "/":
            page_path = page_path[1:]
        if page_path and page_path[-1] == "s":
            page_path = page_path[:-1]
        self.obj_type = page_path

    def page_filename(self):
        if not hasattr(self, "page_name"):
            raise AttributeError("Missing attribute")
        return self.config.page_dir+os.sep+self.page_name

    def read_page(self, page_name=""):
        if not page_name:
            page_filename = self.page_filename()
        else:
            page_filename = self.config.page_dir+os.sep+page_name

        return open(page_filename).read()

    def make_url(self, page_name):
        page_name = re.sub(" ","_",page_name)
        return self.config.url_base+"/"+page_name

    def html_escape(self, str, quote=False):
        str = re.sub("&","&amp;",str)
        str = re.sub("<","&lt;",str)
        str = re.sub(">","&gt;",str)
        str = re.sub('"',"&#34;",str)
        str = re.sub("'","&#39;",str)
        return str

    def add_to_message(self, msg):
        self.message += msg + "<br>\n"

    def add_msg_and_traceback(self, msg):
        self.add_to_message(msg)
        import traceback
        tb = traceback.format_exc()
        self.add_to_message("<pre>\n%s\n</pre>\n" % tb)

    def show_message(self):
        if self.message:
            self.html.append("<h2>lcserver message(s):</h2>")
            self.html.append(self.message)

    def show_html_header(self, title):
        if self.header_shown:
            return

        # new system
        self.header = """Content-type: text/html\n"""
        if self.data.cookies:
            self.header += self.data.cookies + "\n\n"
        else:
            self.header += "\n"

        # render the header markup
        self.html.append(self.header)
        self.html.append("""<head>
<meta charset="utf-8">
<title>%s</title>

<style>
body {
  padding-top: 5px;
  margin-bottom: 100px;
}

html {
  position: relative;
  min-height: 100%%;
}

table {
  border-collapse: collapse;
}

th, td {
  border: 1px solid black;
  padding: 3px 5px 3px;
}

#navbar{width:100%%;}
.alignleft {
  background-color:#0fffff;
  width:180px;
  vertical-align:middle;
  float:left;
}
.alignright {
  background-color:#b0ffb0;
  vertical-align:middle;
  float:right;
}
.center {
/*  background-color:#ff0000; */
  vertical-align:middle;
  width:400px;
  margin:0 auto;
}

</style>
</head>

<body>
""" % title)
        self.header_shown = True

    def show_footer(self):
        if self.footer_shown:
            return
        self.show_message()
        ver_str = self.data.version()
        self.html.append('<hr>\n<p>\n<div align="center"><font size="-2">LabControl server v. %s</font></div>' % ver_str)
        self.html.append("</body>")
        self.footer_shown = True

    def html_error(self, msg):
        return "<font color=red>" + msg + "</font><BR>"

    def send_response(self, result, data):
        self.html.append("Content-type: text/plain\n\n%s\n" % result)
        self.html.append(data)

    # API responses: return python dictionary as json data
    def send_api_response(self, result, data = {}):
        global debug, debug_api_response

        data["result"] = result
        if result == RSLT_FAIL and debug:
            msg = ""
            if "message" in data:
                msg = ": " + data["message"]
            dlog_this("Sending failure response%s" % msg)

        json_data = json.dumps(data, sort_keys=True, indent=4,
            separators=(',', ': '))

        if debug_api_response:
            log_this("response json_data=%s" % json_data)

        self.html.append("Content-type: text/plain\n\n")
        self.html.append(json_data)

    def send_api_response_msg(self, result, msg):
        self.send_api_response(result, { "message": msg })

    def send_api_list_response(self, data):
        resp_data = { "result": "success", "data": data }
        json_data = json.dumps(resp_data, sort_keys=True, indent=4,
            separators=(',', ': '))

        self.html.append("Content-type: text/plain\n\n")
        self.html.append(json_data)

    def get_user(self):
        return self.user.name

    def set_user(self):
        # look up the user using the authorization token and set req.user

        # FIXTHIS (low) - should have a reverse index from auth-token to user name
        # to speed this up.  For now a linear scan of file contents is OK.
        self.user = user_class()


        # There are two ways to set the token, one via the AUTH_TYPE and
        # HTTP_AUTHORIZATION, and the other via HTTP_COOKIE
        # either is valid
        http_auth = self.environ.get("HTTP_AUTHORIZATION", "")
        HTTP_COOKIE = self.environ.get("HTTP_COOKIE", "")

        cookie_token = ""
        auth_token = ""
        auth_type = ""
        if "auth_token=" in HTTP_COOKIE:
            cookie_token = HTTP_COOKIE.split("auth_token=")[1]
            if ";" in cookie_token:
                cookie_token = cookie_token.split(";")[0]
        if http_auth:
            auth_type, auth_token = http_auth.split(" ", 1)
            if auth_type != "token":
                auth_token=""

        # scan user files for matching authentication token

        if auth_token == "not-a-valid-token":
            log_this("Error: HTTP_AUTHORIZATOIN 'not-a-valid-token'")
            return

        dlog_this("cookie_token=%s" % cookie_token)
        dlog_this("auth_token=%s" % auth_token)

        user_dir = self.config.data_dir + "/users"
        try:
            user_files = os.listdir( user_dir )
        except:
            log_this("Error: could not read user files from " + user_dir)
            return

        found_match = False
        for ufile in user_files:
            upath = user_dir + "/" + ufile
            try:
                ufd = open(upath)
            except:
                log_this("Error opening upath %s" % upath)
                continue

            try:
                udata = json.load(ufd)
            except:
                ufd.close()
                log_this("Error reading json data from file %s" % upath)
                continue

            dlog_this("in get_user: udata= %s" % udata)
            utoken = udata.get("auth_token", "not-a-valid-token")

            if cookie_token:
                if cookie_token == utoken:
                    found_match = True
                    break
            elif auth_token == utoken:
                # only check auth_token if cookie_token is not set
                # lc never sets the cookie, only the auth_token
                found_match = True
                break

        if found_match:
            try:
                self.user.name = udata["name"]
            except KeyError:
                log_this("Error: missing 'name' field in user data file %s, in req.set_user()" % upath)
            admin = udata.get("admin","")
            if admin == "True":
                self.user.admin = True
            else:
                self.user.admin = False

        dlog_this("in req.set_user: user=%s" % str(self.user.name))

    def show_live_stream(self, cam_map):
        try:
            url = cam_map["live_stream_url"]
            self.html.append("Content-type: text/html\n")
            self.html.append('<a href="%s">live_stream</a>' % url)
            self.html.append('<br>')
        except KeyError:
            self.html.append("Content-type: text/html\n")
            self.html.append("Error: missing 'live_stream_url' in resource")
            self.html.append('<br>')

# end of req_class
#######################

# response objects are dictionaries with the following schema:
# { "result" : "success" (RSLT_OK),
#    "data" : <command-specific> }
# { "result" : "fail",
#    "message": "reason for failure" }

def show_env(req, env, full=0):
    env_keys = list(env.keys())
    env_keys.sort()

    env_filter=["PATH_INFO", "QUERY_STRING", "REQUEST_METHOD", "SCRIPT_NAME"]
    req.html.append("Here is the environment:")
    req.html.append("<ul>")
    for key in env_keys:
        if full or key in env_filter:
            req.html.append("<li>%s=%s" % (key, env[key]))
    req.html.append("</ul>")

CGI_VARS=["CONTENT_TYPE", "CONTENT_LENGTH", "DOCUMENT_ROOT",
    "HTTP_COOKIE", "HTTP_HOST", "HTTP_REFERER", "HTTP_USER_AGENT",
    "AUTH_TYPE", "HTTP_AUTHORIZATION",
    "HTTPS", "PATH", "QUERY_STRING", "REMOTE_ADDR",
    "REMOTE_HOST", "REMOTE_PORT", "REMOTE_USER", "REQUEST_METHOD",
    "REQUEST_URI", "SCRIPT_FILENAME", "SCRIPT_NAME", "SERVER_ADMIN",
    "SERVER_NAME", "SERVER_PORT", "SERVER_SOFTWARE"]

def show_header(req, title):
    if req.header_shown:
        req.html.append("<h1>" + title + "</h1>")
        return

    req.show_html_header(title)

    # show global navigation bar (including login)
    ## start navbar div
    req.html.append('<div id="navbar" height="100px" width="100%%">')

    # put login link, with float attribute to right, first
    req.html.append("""<div class="alignleft"><font size="6"><b>
    &nbsp;<a href="%(url_base)s">[LC - logo]</b></font></a>&nbsp;""" % req.config)
    req.html.append('</div>')

    # show object menus

    req.html.append("""<div class="alignright">%(login_link_nobr)s</div>""" % req.data)

    req.html.append('<div class="center">')
    req.html.append('<a href="%(url_base)s/boards">Boards</a>&nbsp;' % req.config)
    req.html.append('<a href="%(url_base)s/resources">Resources</a>&nbsp;' % req.config)
    req.html.append('<a href="%(url_base)s/users">Users</a>&nbsp;' % req.config)

    req.html.append('<a href="%(url_base)s/requests">Requests</a>&nbsp;' % req.config)
    req.html.append('<a href="%(url_base)s/logs">Logs</a>&nbsp;' % req.config)
    req.html.append("</div>")

    # close navbar
    req.html.append('</div style="clear: both;">')

    # show page title / page trail
    req.html.append('<h2 id="page_title">%s</h2><hr>' % title)

def log_env(req, varnames=[]):
    env_keys = list(req.environ.keys())
    if varnames:
        env_keys = [item for item in env_keys if item in varnames]
    env_keys.sort()

    log_this("Here is the environment:")
    for key in env_keys:
        log_this("%s=%s" % (key, req.environ[key]))

def do_login_form(req):
    # show user login form
    show_header(req, "LabControl User login")
    req.html.append("""<FORM METHOD="POST" ACTION="%s?action=login">
<table id=loginform><tr><td>
  Name:</td><td align="right"><INPUT type="text" name="name" width=15></input></td></tr>
  <tr><td>Password:</td><td align="right"><INPUT type="password" name="password" width=15></input>
  </td></tr><tr><td> </td><td align="right">
  <INPUT type="submit" name="login" value="Login"></input>
  </td></tr></table></FORM>""" % req.page_url)
    req.html.append("""<br>Please contact %s if you want to create an account""" % req.config.admin_contact_str)
    req.html.append("</td></tr></table>")

def do_login(req):
    # process user login
    name = req.form.getfirst("name", "")
    password = req.form.getfirst("password")
    #req.add_to_message("processing login form: name=%s<br>" % name)

    # check user name and password
    token, reason = authenticate_user(req, name, password)

    # set cookie expiration (to about 1 week - in seconds)
    # FIXTHIS - have authentication cookies last a configured amount of time
    max_age = 604800
    cookies = "auth_token=0;"

    html = ""
    if token:
        html += '<H1 align="center">You successfully logged in!</H1><p>\n'
        html += 'Click to return to <a href="%s">%s</a>' % \
                (req.page_url, req.page_name)
        cookies = "auth_token=%s; Max-Age=%s;" % (token, max_age)
    else:
        html += req.html_error("Invalid login: account or password did not match")

    # send cookies back to user
    req.data.cookies = "Set-Cookie: %s" % cookies

    # process user login
    show_header(req, "LabControl User login")
    req.html.append(html)

def do_logout(req):
    html = '<h1 align="center">You have been logged out</h1>\n'
    html += 'Click here to return to <a href="%s/Main">Main</a>' % req.config.url_base

    cookies = "auth_token=0; expires=Thu, Jan 01 1970 00:00:00 UTC;"

    # send cookies back to user
    req.data.cookies = "Set-Cookie: %s" % cookies

    show_header(req, "LabControl User Account logout")
    req.html.append(html)
    return

################################################################
# user management

def do_manage_users(req):
    # show a list of users, with edit and remove buttons
    # also show a link for adding a user
    users = get_object_list(req, "user")

    show_header(req, "Manage users")

    req.html.append("""Manage LabControl user accounts using the table below.<p>""")

    # show a list of users, with edit and remove buttons
    req.html.append('<table class="users_table">\n<tr>\n')
    req.html.append('  <th>Name</th><th>Is Admin?</th><th>Action:</th></tr>\n')
    for user in users:
        umap = get_object_map(req, "user", user)

        edit_link = req.config.url_base + "/Admin?action=edit_user_admin_form&user=" + user
        remove_link = req.config.url_base + "/Admin?action=remove_user_confirm&user=" + user
        view_link = req.config.url_base + "/Admin?action=view_user_config&user=" + user

        req.html.append('<tr><td valign="top" align="center"><b>%s</b></td>\n' % user)

        admin = umap.get("admin", "False")
        if admin == "True":
            admin_str = "yes"
        else:
            admin_str = ""

        req.html.append('  <td valign="top" align="center"><b>%s</b></td>\n' % admin_str)

        req.html.append('  <td valign="top"><a href="%s">View</a> | <a href="%s">Edit</a> | <a href="%s">Remove</a></td>\n' % (view_link, edit_link, remove_link))
        req.html.append("</tr>\n")

    req.html.append("</table>")

    req.html.append('<p>Or, you can: <a href="%s?action=add_user_form">Add a User</a>' % req.page_url)

    req.html.append('<p><hr><p>Click to return to <a href="%s">%s</a> page' % \
            (req.page_url, req.page_name))

    req.show_footer()

    return

def do_view_user_config(req):
    show_header(req, "View User Config")

    manage_url = "%s?action=manage_users" % req.page_url

    err_close_msg = "<p>Could not view user account.\n<p>" + \
                    'Click to return to <a href="%s">Manage Users</a> page' % \
                         manage_url

    user = req.form.getfirst("user", "")
    if not user:
        msg = "Error: Missing user"
        req.html.append(req.html_error(msg))
        req.html.append(err_close_msg)
        return

    umap = get_object_map(req, "user", user)
    admin = umap.get("admin", "False")
    if admin == "True":
        admin_str = "yes"
    else:
        admin_str = "no"

    auth_token = umap.get("auth_token", "<missing>")

    edit_link = req.config.url_base + "/Admin?action=edit_user_admin_form&user=" + user
    remove_link = req.config.url_base + "/Admin?action=remove_user_confirm&user=" + user

    # show user account data
    req.html.append("Account data for user '<b>%s</b>'" % user)
    req.html.append('<table class="users_table">')
    req.html.append("""
      <tr><td>Name</td><td>%s</td></tr>
      <tr><td>Password</td><td>XXXXXXXX</td></tr>
      <tr><td>Is Admin</td><td>%s</td></tr>
      <tr><td>Auth Token</td><td>%s</td></tr>
</table>
""" % (user, admin_str, auth_token))

    req.html.append('<p><a href="%s">Edit User account</a> | <a href="%s">Remove this User account</a>\n' % (edit_link, remove_link))

    req.html.append('<p><hr><p>Click to return to <a href="%s">Manage Users</a> page' % manage_url)

    return

# The user is not allowed to edit everything the admin can edit
# and the info on a 'create' form is different than the info on
# an 'edit' form.  This makes this routine a bit tricky.
def user_form(req, action, umap, user_is_self=False):
    html = """<FORM METHOD="POST" ACTION="%s?action=%s">
<table id=create_user_form>
""" % (req.page_url, action)

    if action == "add_user":
        html += """<tr><td>Name:</td><td><INPUT type="text" name="name" width=30></input></td></tr>"""
        button_label = "Create User Account"
    else:
        name = umap["name"]
        html += """<tr><td>Name:</td><td><b>%s</b>
          <INPUT type="hidden" name="name" value="%s"></input>
          </td></tr>\n""" % (name, name)
        button_label = "Update User Account"

    admin = umap.get("admin", "False")
    if admin == "True":
        admin_str = "checked"
    else:
        admin_str = ""

    auth_token = umap.get("auth_token", "")

    html += """<tr><td>Password:</td><td>
    <INPUT type="password" name="password" width=30></input>
  </td></tr>
  <tr><td>Password (repeat):</td><td>
    <INPUT type="password" name="password2" width=30></input>
  </td></tr>"""

    if user_is_self:
        # user can't change their own admin status
        html += "<tr><td>Is Admin?</td><td>%s</td></tr>" % admin
    else:
        html += """
  <tr><td>Is Admin?</td><td>
    <INPUT type="checkbox" name="admin" value="True" %s></input>
  </td></tr>""" % admin_str

    # On create, an auth_token is generated by do_add_user()
    # so, it's not displayed on the 'create' form
    if action == "update_user":
        if user_is_self:
            # The user can't update the token themselves, but can see it.
            html += """<tr><td>Auth Token:</td><td>%s</td></tr>""" % auth_token
        else:
            # an administrator can edit it
            html += """<tr><td>Auth Token:</td><td align="right">
          <INPUT type="text" name="auth_token" value="%s" width=60></input>
  </td></tr>\n""" % auth_token

    html += """
    <tr><td> </td>
    <td> <INPUT type="submit" value="%s"></input>
    <a href="%s">Cancel</a></input></td></tr>
</table></FORM>""" % (button_label, req.page_url)

    return html


def do_add_user_form(req):
    # show create user login form
    show_header(req, "Create LabControl User Account")
    req.html.append("""Please enter the data for the new user.
<p>Note: Names may only include letters, numbers, and the following
characters: '_', '-', '.', '@'
<p>
""")

    umap = { "name": "" }
    req.html.append(user_form(req, "add_user", umap))

    req.html.append('<p>Click to return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))

def do_edit_user_admin_form(req):
    # show user edit form
    show_header(req, "LabControl User Account edit")

    name = req.form.getfirst("user", "")
    if not name:
        msg = "Error: missing user name"
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_msg)
        return

    req.html.append("""Edit the user by editing the fields below.<br>
    If the password fields are left blank, the password is not changed.<br>
    To generate a new random Auth Token, put the word 'new' in the input
    field.""")

    umap = get_object_map(req, "user", name)
    req.html.append(user_form(req, "update_user", umap, False))

    req.html.append('<p><hr>Click to return to <a href="%s?action=manage_users">Manage Users</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))

def do_edit_user_user_form(req):
    # show user edit form
    # this can only be used for changing a user's own attributes
    show_header(req, "LabControl User Account edit")

    req.html.append("""Edit the user by editing the fields below.<br>
    If the password fields are left blank,
    the password is not changed.""")

    name = req.user.name
    umap = get_object_map(req, "user", name)
    req.html.append(user_form(req, "update_user", umap, True))

    req.html.append('<p><hr>Return to <a href="%s">%s</a> page.' %
                         (req.page_url, req.page_name))

def do_add_user(req):
    show_header(req, "LabControl Create User")

    manage_url = "%s?action=manage_users" % req.page_url

    err_close_msg = "<p>Could not create user.\n<p>" + \
                    'Go "Back" to return to the "add user" form<br>' \
                    'Or click to return to <a href="%s">Manage Users</a> page' % \
                         manage_url

    # process create user action
    name = req.form.getfirst("name", "")
    password = req.form.getfirst("password", "not-provided")
    password2 = req.form.getfirst("password2", "not-provided2")
    admin = req.form.getfirst("admin", "False")
    dlog_this("name=%s" % name)
    dlog_this("admin=%s" % admin)

    # check user name and password
    # see if user name has weird chars
    still_ok = True
    allowed_re_pat = "^[\w_.@-]+$"
    if not re.match(allowed_re_pat, name):
        msg = "Error: user name '%s' has disallowed chars.  Only letters, numbers and _, -, . and @ are allowed." % name
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_close_msg)
        return

    # make sure user doesn't already exist
    users = get_object_list(req, "user")
    if name in users:
        msg = "Error: user account '%s' already exists." % name
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_close_msg)
        return

    # make sure that passwords match
    if password != password2:
        msg = "Error: passwords do not match!"
        log_this(msg)
        req.html.append(req.html_error(msg))
        req.html.append(err_close_msg)
        return

    # process admin flag
    if admin == "True":
        log_this("Creating user account '%s' with admin privileges!" % name)

    # create auth_token
    auth_token = str(uuid.uuid4())

    # everything looks OK, save the data
    umap = { "name": name, "password": password, "auth_token": auth_token,
            "admin": admin }

    # save the data to the json file
    msg = save_object_data(req, "user", name, umap)

    if msg:
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_close_msg)
        return

    req.html.append('You successfully created user account: %s\n<p>\n' % name)
    req.html.append('Click to return to <a href="%s">Manage Users</a>' % \
            manage_url)
    return

def do_update_user(req):
    show_header(req, "Update User")

    manage_url = "%s?action=manage_users" % req.page_url

    err_close_msg = "<p>Could not update user.\n<p>" + \
                    'Click to return to <a href="%s">Manage Users</a> page' % \
                         manage_url

    # process create user action
    name = req.form.getfirst("name", "")
    password = req.form.getfirst("password", "not-provided")
    password2 = req.form.getfirst("password2", "not-provided2")
    admin = req.form.getfirst("admin", "False")
    auth_token = req.form.getfirst("auth_token", "not-provided")

    dlog_this("name=%s" % name)
    dlog_this("admin=%s" % admin)

    # make sure user already exists
    users = get_object_list(req, "user")
    if name not in users:
        msg = "Error: user account '%s' does not exists." % name
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_close_msg)
        return

    umap = get_object_map(req, "user", name)

    # update password, if the user supplied one
    if password and password != "not-provided":
        # make sure that passwords match
        if password != password2:
            msg = "Error: passwords do not match!"
            log_this(msg)
            req.html.append(req.html_error(msg))
            req.html.append(err_close_msg)
            return
        umap["password"] = password

    # process admin flag
    umap["admin"] = admin

    # process the token
    if auth_token != "not-provided":
        if auth_token == "new":
            auth_token = str(uuid.uuid4())

        umap["auth_token"] = auth_token

    # save data back to json file
    msg = save_object_data(req, "user", name, umap)

    if msg:
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_close_msg)
        return

    req.html.append('You successfully updated user account: %s\n<p>\n' % name)
    req.html.append('Click to return to <a href="%s">Manage Users</a>' % \
            manage_url)

    return

def do_remove_user_confirm(req):
    show_header(req, "Confirm User Removal")

    manage_url = "%s?action=manage_users" % req.page_url

    user = req.form.getfirst("user", "")
    if not user:
        msg = "Error: missing user to confirm removal of"
        req.html.append(req.html_error(msg))
        req.html.append("Could not remove user" + \
              'Click to return to <a href="%s">Manage Users</a> page' % \
                         manage_url)
        return

    # ask for confirmation before removing the user
    req.html.append("Confirm that you really want to remove the user account for:<br><ul><b>%s</b></ul>\n<p>" % user)
    req.html.append("""<FORM METHOD="POST" ACTION="%s?action=remove_user&user=%s">
<INPUT type="submit" name="removeuser" value="Remove User Account"></input>
<a href="%s">Cancel</a>
</FORM>""" % (req.page_url, user, manage_url))
    req.html.append('<p>Click to return to <a href="%s">Manage Users</a> page' %  manage_url)
    return


def do_remove_user(req):
    show_header(req, "Remove User")

    user = req.form.getfirst("user", "")
    manage_url = "%s?action=manage_users" % req.page_url
    if not user:
        msg = "Error: missing user to confirm removal of"
        req.html.append(req.html_error(msg))
        req.html.append("Could not remove user" + \
              'Click to return to <a href="%s">Manage Users</a>' % manage_url)
        return

    file_path = "%s/users/user-%s.json" %  (req.config.data_dir, user)

    msg = None
    try:
        os.remove(file_path)
        log_this("Removed file %s" % file_path)
    except OSError:
        msg = "Error: Could not remove user file for '%s'" % user

    # Remove any reservations held by this user
    if not msg:
        boards = get_object_list(req, "board")
        for board in boards:
            bmap = get_object_map(req, "board", board)
            if bmap.get("AssignedTo", "nobody") == user:
                clear_reservation(req, bmap)

    if msg:
        req.html.append(req.html_error(msg))
    else:
        req.html.append("User account '<b>%s</b>' removed." % user)

    req.html.append('<p>Click to return to <a href="%s">Manage Users</a> page' % manage_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         req.page_url)
    return


################################################################
# board management

def do_manage_boards(req):
    # show a list of boards, with edit and remove buttons
    # also show a link for adding a board
    boards = get_object_list(req, "board")

    show_header(req, "Manage boards")

    req.html.append("""Manage LabControl boards using the table below.<p>""")

    # show a list of boards, with edit and remove buttons
    req.html.append('<table class="boards_table">\n<tr>\n')
    req.html.append("  <th>Name</th><th>Description</th><th>Action:</th>\n</tr>\n")
    for board in boards:
        req.html.append("<tr>\n")
        bmap = get_object_map(req, "board", board)
        description = bmap.get("description", "")
        edit_link = req.config.url_base + "/Admin?action=edit_board_form&board=" + board
        remove_link = req.config.url_base + "/Admin?action=remove_board_confirm&board=" + board
        view_link = req.config.url_base + "/Admin?action=view_board_config&board=" + board

        req.html.append('  <td valign="top" align="center"><b>%s</b></td>\n' % board)
        req.html.append('  <td valign="top">%s</td>\n' % description)
        req.html.append('  <td valign="top"><a href="%s">View</a> | <a href="%s">Edit</a> | <a href="%s">Remove</a></td>\n' % (view_link, edit_link,remove_link))
        req.html.append("</tr>\n")

    req.html.append("</table>")

    req.html.append('<p>Or, you can: <a href="%s?action=add_board_form">Add a board</a>' % req.page_url)

    req.html.append('<p><hr><p>Click to return to <a href="%s">%s</a> page' % \
            (req.page_url, req.page_name))

    req.show_footer()


###########################
# here are the global field lists for boards
board_field_list = ["description", "ip_addr", "host", "user", "password",
        "power_controller", "power_measurement", "serial_endpoints",
        "power_port", "start_time", "end_time"]

board_cmd_field_list = ["command_status_cmd", "network_status_cmd",
        "run_cmd", "upload_cmd", "download_cmd", "login_cmd"]


def do_view_board_config(req):
    show_header(req, "View Board Config")

    manage_url = "%s?action=manage_boards" % req.page_url

    err_close_msg = "<p>Could not view board config.\n<p>" + \
                    'Click to return to <a href="%s">Manage Boards</a> page' % \
                         manage_url

    name = req.form.getfirst("board", "")
    if not name:
        msg = "Error: Missing board"
        req.html.append(req.html_error(msg))
        req.html.append(err_close_msg)
        return

    bmap = get_object_map(req, "board", name)

    edit_link = req.config.url_base + "/Admin?action=edit_board_form&board=" + name
    remove_link = req.config.url_base + "/Admin?action=remove_board_confirm&board=" + name

    # show board data
    req.html.append("Configuration data for board '<b>%s</b>'" % name)
    req.html.append('<table class="board_table">')

    for field in board_field_list:
        value = bmap.get(field, "")
        if field in fields_that_are_lists:
            value = ", ".join(value)
            field += "(s)"
        req.html.append("<tr><td>%s</td><td>%s</td></tr>\n" % (field, req.html_escape(value)))

    for cmd_field in board_cmd_field_list:
        value = bmap.get(cmd_field, "")
        req.html.append("<tr><td>%s</td><td>%s</td></tr>\n" % (cmd_field, req.html_escape(value)))

    req.html.append("</table>")

    req.html.append('<p><a href="%s">Edit Board Config</a> | <a href="%s">Remove this Board</a>\n' % (edit_link, remove_link))

    req.html.append('<p><hr><p>Click to return to <a href="%s">Manage Boards</a> page' % manage_url)

    return

def do_add_board_form(req):
    # show form for adding a board
    show_header(req, "Create LabControl Board")
    req.html.append("""Please enter the data for the new board.
<p><i>Note: Names may only include letters, numbers, and the following
characters: _ - . @<i>
<p>
""")

    # can provide default values here, just provide an empty name for now
    bmap = { "name": "", "description": "" }
    req.html.append(board_form(req, "add_board", bmap))

    req.html.append('<p>Click to return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))

def do_add_board(req):
    show_header(req, "Add Board")

    err_msg = "<p>Could not create board.\n<p>" + \
                    'Click to return to <a href="%s">%s</a>' % \
                         (req.page_url, req.page_name)

    # process new board data
    name = req.form.getfirst("name", "")
    log_this("Trying to create board: %s" % name)

    if not name:
        msg = "Error: empty board name '%s'" % name
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_msg)
        return

    still_ok = True
    allowed_re_pat = "^[\w_-]+$"
    if not re.match(allowed_re_pat, name):
        msg = "Error: board name '%s' has disallowed chars.  Only letters, numbers, _, and are allowed." % name
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_msg)
        return

    # make sure the name doesn't already exist
    boards = get_object_list(req, "board")
    if name in boards:
        msg = "Error: board '%s' already exists." % name
        log_this(msg)
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    board_map = {"name": name, "description": "", "AssignedTo": "nobody"}
    for field in board_field_list:
        if field == "name":
            continue
        if field in fields_that_are_lists:
            value = req.form.getlist(field)
            if "None" in value:
                value = []
                board_map[field] = value
        else:
            value = req.form.getfirst(field, "")
            if value == "None":
                value = ""
                board_map[field] = value
        if value:
            board_map[field] = value

    for cmd_field in board_cmd_field_list:
        value = req.form.getfirst(cmd_field, "")
        if value:
            board_map[cmd_field] = value

    msg = save_object_data(req, "board", name, board_map)
    if msg:
        log_this(msg)
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    req.html.append('You successfully created board: %s\n<p>\n' % name)
    req.html.append('<p>Click to return to <a href="%s?action=manage_boards">Manage Boards</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))
    return

def board_form(req, action, bmap):
    html = """<FORM METHOD="POST" ACTION="%s?action=%s">
<table id=create_board_form>
""" % (req.page_url, action)

    if action == "add_board":
        html += """<tr><td>Name:</td><td align="right"><INPUT type="text" name="name" width=15></input></td></tr>"""
    else:
        name = bmap["name"]
        html += """<tr><td>Name:</td><td><b>%s</b>
          <INPUT type="hidden" name="name" value="%s"></input>
          </td></tr>
""" % (name, name)

    # need to html_escape the values for the form fields
    # in case they have wonky chars

    for field in board_field_list:
        if field == "serial_endpoints":
            html += "<tr><td>serial_endpoints:</td><td>\n"
            serial_resources = get_resource_list_by_type(req, "serial")
            serial_resources.append("None")
            serials = bmap.get(field, [])
            html += gen_list_form_element(field, serial_resources, serials)
            html += "</td></tr>\n"
            continue
        if field == "power_controller":
            html += "<tr><td>power_controller:</td><td>\n"
            pc_resources = get_resource_list_by_type(req, "power_controller")
            pc_resources.append("None")
            pc = bmap.get(field, "None")
            if not pc:
                pc = "None"
            html += gen_select_form_element(field, pc_resources, pc)
            html += "</td></tr>\n"
            continue
        if field == "power_measurement":
            html += "<tr><td>power_measurement:</td><td>\n"
            pm_resources = get_resource_list_by_type(req, "power_measurement")
            pm_resources.append("None")
            pm = bmap.get(field, "None")
            if not pm:
                pm = "None"
            html += gen_select_form_element(field, pm_resources, pm)
            html += "</td></tr>\n"
            continue
        if field == "audio_endpoints":
            html += "<tr><td>audio_endpoints:</td><td>\n"
            audio_resources = get_resource_list_by_type(req, "audio")
            audio_resources.append("None")
            audios = bmap.get(field, [])
            html += gen_list_form_element(field, audio_resources, audios)
            html += "</td></tr>\n"
            continue

        value = bmap.get(field, "")
        html += """<tr><td>%s:</td><td align="right"><INPUT type="text" name="%s" value="%s" width=40></input></td></tr>
""" % (field, field, req.html_escape(value, True))

    for cmd_field in board_cmd_field_list:
        value = bmap.get(cmd_field, "")
        dlog_this("cmd_field %s value=%s" % (cmd_field, value))
        html += """<tr><td>%s:</td><td align="right"><INPUT type="text" name="%s" value="%s" width=100></input></td></tr>
""" % (cmd_field, cmd_field, req.html_escape(value, True))

    if action == "add_board":
        button_label = "Create Board"
    else:
        button_label = "Update Board"

    html += """
    <tr><td> </td>
    <td> <INPUT type="submit" value="%s"></input>
    <a href="%s">Cancel</a></input></td></tr>
</table></FORM>""" % (button_label, req.page_url)

    return html


def do_edit_board_form(req):
    show_header(req, "Edit Board")

    err_msg = """Could not show edit board form<br>
Click to return to <a href="%s?action=manage_boards">"Manage Boards</a> page
""" % req.page_url

    name = req.form.getfirst("board", "")
    if not name:
        msg = "Error: missing name of board to add"
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    bmap = get_object_map(req, "board", name)
    if not bmap:
        msg = "Error: invalid board name '%s'" % name
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    req.html.append(board_form(req, "update_board", bmap))

    req.html.append('<p>Click to return to <a href="%s?action=manage_boards">Manage Boards</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))

def do_update_board(req):
    show_header(req, "Update Board")

    err_msg = "<p>Could not update board.\n<p>" + \
                    'Click to return to <a href="%s">%s</a>' % \
                         (req.page_url, req.page_name)

    # process updated data
    name = req.form.getfirst("name", "")
    log_this("Updating board: '%s'" % name)

    board_map = get_object_map(req, "board", name)

    for field in board_field_list:
        if field == "name":
            continue
        if field in fields_that_are_lists:
            value = req.form.getlist(field)
            if "None" in value:
                value = []
                board_map[field] = value
        else:
            value = req.form.getfirst(field, "")
            if value == "None":
                value = ""
                board_map[field] = value
        if value:
            board_map[field] = value

    for cmd_field in board_cmd_field_list:
        value = req.form.getfirst(cmd_field, "")
        if value:
            board_map[cmd_field] = value

    msg = save_object_data(req, "board", name, board_map)
    if msg:
        log_this(msg)
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    req.html.append('You successfully updated board: %s\n<p>\n' % name)
    req.html.append('<p>Click to <a href="%s?action=view_board_config&board=%s">View Board Config</a> for this board' %  (req.page_url, name))
    req.html.append('<p>Click to return to <a href="%s?action=manage_boards">Manage Boards</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))
    return

def do_remove_board_confirm(req):
    show_header(req, "Confirm board removal")

    board = req.form.getfirst("board", "")
    if not board:
        msg = "Error: missing board to confirm removal of"
        req.html.append(req.html_error(msg))
        req.html.append("Could not remove board" + \
              'Click to return to <a href="%s">%s</a>' % \
                         (req.page_url, req.page_name))
        return

    bmap = get_object_map(req, "board", board)
    log_this("bmap=%s" % bmap)
    desc = bmap["description"]

    # ask for confirmation before removing the board
    req.html.append("Confirm that you really want to remove board '%s'\n<p>" % board)
    req.html.append("Description: '%s'\n<p>" % desc)
    req.html.append("""<FORM METHOD="POST" ACTION="%s?action=remove_board&board=%s">
<INPUT type="submit" name="removeboard" value="Remove Board"></input>
<a href="%s">Cancel</a>
</FORM>""" % (req.page_url, board, req.page_url))
    req.html.append('<p>Click to return to <a href="%s?action=manage_boards">Manage Boards</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))
    return


def do_remove_board(req):
    show_header(req, "Remove Board")

    board = req.form.getfirst("board", "")
    if not board:
        msg = "Error: missing board to confirm removal of"
        req.html.append(req.html_error(msg))
        req.html.append("Could not remove board" + \
              'Click to return to <a href="%s">%s</a>' % \
                         (req.page_url, req.page_name))
        return

    file_path = "%s/boards/board-%s.json" %  (req.config.data_dir, board)

    msg = None
    try:
        os.remove(file_path)
        log_this("Removed file %s" % file_path)
    except OSError:
        msg = "Error: Could not remove board file for '%s'" % board

    if msg:
        req.html.append(req.html_error(msg))
    else:
        req.html.append("Board '%s' removed." % board)

    req.html.append('<p>Click to return to <a href="%s?action=manage_boards">Manage Boards</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))
    return


################################################################
# resource management

def do_manage_resources(req):
    # show a list of resources, with edit and remove buttons
    # also show a link for adding a resource

    resources = get_object_list(req, "resource")

    show_header(req, "Manage Resources")

    req.html.append("""Manage LabControl resources using the table below.<p>""")

    # show a list of boards, with edit and remove buttons
    req.html.append('<table class="resourcess_table">\n<tr>\n')
    req.html.append("  <th>Name</th><th>Type(s)</th><th>Description</th><th>Action:</th>\n</tr>\n")
    for resource in resources:
        req.html.append("<tr>\n")
        rmap = get_object_map(req, "resource", resource)
        description = rmap.get("description", "")
        res_types = rmap.get("type", [])
        types_str = ", ".join(res_types)
        edit_link = req.config.url_base + "/Admin?action=edit_resource_form&resource=" + resource
        remove_link = req.config.url_base + "/Admin?action=remove_resource_confirm&resource=" + resource
        view_link = req.config.url_base + "/Admin?action=view_resource_config&resource=" + resource

        req.html.append('  <td valign="top" align="center"><b>%s</b></td>\n' % resource)
        req.html.append('  <td valign="top">%s</td>\n' % types_str)
        req.html.append('  <td valign="top">%s</td>\n' % description)
        req.html.append('  <td valign="top"><a href="%s">View</a> | <a href="%s">Edit</a> | <a href="%s">Remove</a></td>\n' % (view_link, edit_link,remove_link))
        req.html.append("</tr>\n")

    req.html.append("</table>")

    req.html.append('<p>Or, you can: <a href="%s?action=add_resource_form">Add a resource</a>' % req.page_url)

    req.html.append('<p><hr><p>Click to return to <a href="%s">%s</a> page' % \
            (req.page_url, req.page_name))

    req.show_footer()

###########################
# here are the global field lists for resources
resource_field_list = ["description", "res_host", "res_user", "res_password",
        "type", "res_ip", "video_filename_extension", "reservation",
        "sampling_interval", "serial_dev",
        "power_port", "board", "board_feature",
        "live_stream_url", "video_filename_extension",
        "format", "rate", "device_name"]

resource_cmd_field_list = ["on_cmd", "off_cmd", "status_cmd",
        "reboot_cmd", "capture_cmd", "image_cmd", "video_cmd",
        "image_capture_cmd", "start_cmd", "stop_cmd", "config_cmd",
        "put_cmd", "live_stream_start_cmd", "live_stream_stop_cmd"]

resource_known_fields = resource_field_list + resource_cmd_field_list
resource_known_fields.append("name")

res_types = ["power-controller", "power-measurement", "serial", "camera",
        "canbus", "server", "audio"]

fields_that_are_lists = ["type", "serial_endpoints", "audio_outputs"]

pc_cmds = ["on_cmd", "off_cmd", "status_cmd", "reboot_cmd"]
pm_cmds = ["start_cmd", "stop_cmd"]
serial_cmds = ["config_cmd", "start_cmd", "stop_cmd", "put_cmd"]
canbus_cmds = ["config_cmd", "start_cmd", "stop_cmd", "put_cmd"]
camera_cmds = ["image_cmd", "video_cmd", "image_capture_cmd",
                "config_cmd", "start_cmd", "stop_cmd",
                "live_stream_start", "live_stream_stop"]
audio_cmds = ["config_cmd", "start_cmd", "stop_cmd", "put_cmd"]
server_cmds = ["start_cmd", "stop_cmd"]

def do_view_resource_config(req):
    show_header(req, "View Resource Config")

    manage_url = "%s?action=manage_resources" % req.page_url

    err_close_msg = "<p>Could not view resource config.\n<p>" + \
                    'Click to return to <a href="%s">Manage Resources</a> page' % \
                         manage_url

    name = req.form.getfirst("resource", "")
    if not name:
        msg = "Error: Missing resource"
        req.html.append(req.html_error(msg))
        req.html.append(err_close_msg)
        return

    rmap = get_object_map(req, "resource", name)

    edit_link = req.config.url_base + "/Admin?action=edit_resource_form&resource=" + name
    remove_link = req.config.url_base + "/Admin?action=remove_resource_confirm&resource=" + name

    # show resource data
    req.html.append("Configuration data for resource '<b>%s</b>'" % name)
    req.html.append('<table class="resource_table">')

    # FIXTHIS - use type-specific field lists here (in do_view_board_config)

    for field in resource_field_list:
        value = rmap.get(field, "")
        if field in fields_that_are_lists:
            value = ", ".join(value)
            field += "(s)"
        req.html.append("<tr><td>%s</td><td>%s</td></tr>\n" % (field, req.html_escape(str(value))))

    for cmd_field in resource_cmd_field_list:
        value = rmap.get(cmd_field, "")
        req.html.append("<tr><td>%s</td><td>%s</td></tr>\n" % (cmd_field, req.html_escape(value)))

    req.html.append("</table>")

    req.html.append("<p>User-defined (ad-hoc) fields:")
    req.html.append('<table class="resource_table">')

    # show miscelaneous (user-defined) fields
    for field in list(rmap.keys()):
        if field in resource_known_fields:
            continue
        value = rmap.get(field)
        req.html.append("<tr><td>%s</td><td>%s</td></tr>\n" % (field, req.html_escape(str(value))))

    req.html.append("</table>")

    req.html.append('<p><a href="%s">Edit Resource Config</a> | <a href="%s">Remove this Resource</a>\n' % (edit_link, remove_link))

    req.html.append('<p><hr><p>Click to return to <a href="%s">Manage Resources</a> page' % manage_url)

    return


def do_add_resource_form(req):
    # show form for adding a resource
    show_header(req, "Create LabControl Resource")
    req.html.append("""Please enter the data for the new resource.
<p><i>Note: Names may only include letters, numbers, and the following
characters: _ - . @<i>
<p>
""")

    # can provide default values here
    # just provide an empty name and description for now
    rmap = { "name": "", "description": "" }
    req.html.append(resource_form(req, "add_resource", rmap))

    req.html.append('<p>Click to return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))

def gen_select_form_element(field, value_options, value):
    html = '<select name="%s">\n' % field
    for option in value_options:
        if option == value:
            sel = " selected"
        else:
            sel = ""
        html += '  <option value="%s"%s>%s</option>\n' % (option, sel, option)
    html += "</select>"
    return html

# returns html text for a form element for a list resource
# this supports multiple section
# value_options has the list of all possible options
# values indicates the list of already selection options
def gen_list_form_element(field, value_options, values):
    dlog_this("value_options=%s" % value_options)
    dlog_this("values=%s" % values)

    html = '<select name="%s" multiple>\n' % field
    for option in value_options:
        if option in values:
            sel = " selected"
        else:
            sel = ""
        html += '  <option value="%s"%s>%s</option>\n' % (option, sel, option)
    html += "</select>"
    return html

def resource_form(req, action, rmap):
    html = """<FORM METHOD="POST" ACTION="%s?action=%s">
<table id=create_board_form>
""" % (req.page_url, action)

    if action == "add_resource":
        html += """<tr><td>Name:</td><td align="right"><INPUT type="text" name="name" width=15></input></td></tr>"""
    else:
        name = rmap["name"]
        html += """<tr><td>Name:</td><td><b>%s</b>
          <INPUT type="hidden" name="name" value="%s"></input>
          </td></tr>
""" % (name, name)

    # html_escape the values for the form fields
    # in case they have wonky chars

    for field in resource_field_list:
        if field == "type":
            html += "<tr><td>type:</td><td>\n"
            cur_type = rmap.get(field, "")
            html += gen_list_form_element(field, res_types, cur_type)
            html += "</td></tr>\n"
            continue

        value = rmap.get(field, "")
        html += """<tr><td>%s:</td><td align="right"><INPUT type="text" name="%s" value="%s" width=40></input></td></tr>
""" % (field, field, req.html_escape(value, True))

    for cmd_field in resource_cmd_field_list:
        value = rmap.get(cmd_field, "")
        dlog_this("cmd_field %s value=%s" % (cmd_field, value))
        html += """<tr><td>%s:</td><td align="right"><INPUT type="text" name="%s" value="%s" width=100></input></td></tr>
""" % (cmd_field, cmd_field, req.html_escape(value, True))

    # FIXTHIS - show already-defined user-defined fields

    # allow inputting new user-defined fields
    for i in range(10):
        name_field = "name%d" % i
        value_field = "value%d" % i
        html += """<tr><td><INPUT type="text" name="%s" value="">:</td>
        <td align="right"><INPUT type="text" name="%s" value="" width=100></input></td></tr>
""" % (name_field, value_field)

    if action == "add_resource":
        button_label = "Create Resource"
    else:
        button_label = "Update Resource"

    html += """
    <tr><td> </td>
    <td> <INPUT type="submit" value="%s"></input>
    <a href="%s">Cancel</a></input></td></tr>
</table></FORM>""" % (button_label, req.page_url)

    return html


def do_add_resource(req):
    show_header(req, "Add Resource")

    err_msg = "<p>Could not create resource.\n<p>" + \
                    'Click to return to <a href="%s">%s</a>' % \
                         (req.page_url, req.page_name)

    # process new resource data
    name = req.form.getfirst("name", "")
    log_this("Trying to create resource: %s" % name)

    if not name:
        msg = "Error: empty resource name '%s'" % name
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_msg)
        return

    still_ok = True
    allowed_re_pat = "^[\w_-]+$"
    if not re.match(allowed_re_pat, name):
        msg = "Error: resource name '%s' has disallowed chars.  Only letters, numbers, _, and are allowed." % name
        log_this(msg)
        req.html.append(req.html_error(req.html_escape(msg)))
        req.html.append(err_msg)
        return

    # make sure the name doesn't already exist
    resources = get_object_list(req, "resource")
    if name in resources:
        msg = "Error: resource '%s' already exists." % name
        log_this(msg)
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    rmap = {"name": name, "description": ""}
    for field in resource_field_list:
        if field == "name":
            continue
        if field == "type":
            # get (possibly) multiple values, and store as a list
            value = req.form.getlist(field)
        else:
            value = req.form.getfirst(field, "")
        if value:
            rmap[field] = value

    for cmd_field in resource_cmd_field_list:
        value = req.form.getfirst(cmd_field, "")
        if value:
            rmap[cmd_field] = value

    # check for new user-defined fields
    for i in range(10):
        field = req.form.getfirst("name%d" % i, "")
        value = req.form.getfirst("value%d" % i, "")
        if field and value:
            rmap[field] = value

    msg = save_object_data(req, "resource", name, rmap)
    if msg:
        log_this(msg)
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    req.html.append('You successfully created resource: %s\n<p>\n' % name)
    req.html.append('<p>Click to return to <a href="%s?action=manage_resources">Manage Resources</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))
    return


def do_edit_resource_form(req):
    show_header(req, "Edit Resource")

    err_msg = """Could not show edit resource form<br>
Click to return to <a href="%s?action=manage_resources">"Manage Resources</a> page
""" % req.page_url

    name = req.form.getfirst("resource", "")
    if not name:
        msg = "Error: missing name of resource to add"
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    rmap = get_object_map(req, "resource", name)
    if not rmap:
        msg = "Error: invalid resource name '%s'" % name
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    req.html.append(resource_form(req, "update_resource", rmap))

    req.html.append('<p>Click to return to <a href="%s?action=manage_resources">Manage Resources</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))


def do_update_resource(req):
    show_header(req, "Update Resource")

    err_msg = "<p>Could not update resource.\n<p>" + \
                    'Click to return to <a href="%s">%s</a>' % \
                         (req.page_url, req.page_name)

    # process updated data
    name = req.form.getfirst("name", "")
    log_this("Updating resource: '%s'" % name)

    rmap = get_object_map(req, "resource", name)

    for field in resource_field_list:
        if field == "name":
            continue
        if field == "type":
            # get multiple values
            value = req.form.getlist(field)
        else:
            value = req.form.getfirst(field, "")
        if value:
            rmap[field] = value

    for cmd_field in resource_cmd_field_list:
        value = req.form.getfirst(cmd_field, "")
        if value:
            rmap[cmd_field] = value

    # handle previously defined user-defined fields
    for field in list(rmap.keys()):
        if field in resource_known_fields:
            continue
        value = req.form.getfirst(field, "")

    # check for new user-defined fields
    for i in range(10):
        field = req.form.getfirst("name%d" % i, "")
        value = req.form.getfirst("value%d" % i, "")
        if field and value:
            rmap[field] = value

    msg = save_object_data(req, "resource", name, rmap)
    if msg:
        log_this(msg)
        req.html.append(req.html_error(msg))
        req.html.append(err_msg)
        return

    req.html.append('You successfully updated resource: %s\n<p>\n' % name)
    req.html.append('<p>Click to return to <a href="%s?action=manage_resources">Manage Resources</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))


def do_remove_resource_confirm(req):
    show_header(req, "Confirm Resource Removal")

    name = req.form.getfirst("resource", "")
    if not name:
        msg = "Error: missing resource to confirm removal of"
        req.html.append(req.html_error(msg))
        req.html.append("Could not remove resource" + \
              'Click to return to <a href="%s">%s</a>' % \
                         (req.page_url, req.page_name))
        return

    rmap = get_object_map(req, "resource", name)
    dlog_this("rmap=%s" % rmap)
    desc = rmap["description"]

    # ask for confirmation before removing the resource
    req.html.append("Confirm that you really want to remove resource '%s'\n<p>" % name)
    req.html.append("Description: '%s'\n<p>" % desc)
    req.html.append("""<FORM METHOD="POST" ACTION="%s?action=remove_resource&resource=%s">
<INPUT type="submit" name="removeresource" value="Remove Resource"></input>
<a href="%s">Cancel</a>
</FORM>""" % (req.page_url, name, req.page_url))
    req.html.append('<p>Click to return to <a href="%s?action=manage_resources">Manage Resources</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))


def do_remove_resource(req):
    show_header(req, "Remove Resource")

    name = req.form.getfirst("resource", "")
    if not name:
        msg = "Error: missing resource to confirm removal of"
        req.html.append(req.html_error(msg))
        req.html.append("Could not remove resource" + \
              'Click to return to <a href="%s">%s</a>' % \
                         (req.page_url, req.page_name))
        return

    file_path = "%s/resources/resource-%s.json" %  (req.config.data_dir, name)

    msg = None
    try:
        os.remove(file_path)
        log_this("Removed file %s" % file_path)
    except OSError:
        msg = "Error: Could not remove resource file for '%s'" % name

    if msg:
        req.html.append(req.html_error(msg))
    else:
        req.html.append("Board '%s' removed." % name)

    req.html.append('<p>Click to return to <a href="%s?action=manage_resources">Manage Resources</a> page' %  req.page_url)
    req.html.append('<p>Return to <a href="%s/Admin">Admin</a> page.' %
                         (req.page_url))
    return


def get_timestamp():
    t = time.time()
    tfrac = int((t - int(t))*100)
    timestamp = time.strftime("%Y-%m-%d_%H:%M:%S.") + "%02d" % tfrac
    return timestamp

def save_file(req, file_field, upload_dir):
    # some debugging...
    F = RSLT_FAIL
    msg = ""

    #msg += "req.form=\n"
    #for k in list(req.form.keys()):
    #   msg += "%s: %s\n" % (k, req.form[k])

    if not file_field in req.form:
        return F, msg+"Form is missing key %s\n" % file_field, ""

    fileitem = req.form[file_field]
    if not fileitem.file:
        return F, msg+"fileitem has no attribute 'file'\n", ""

    if not fileitem.filename:
        return F, msg+"fileitem has no attribute 'filename'\n", ""

    filepath = upload_dir + os.sep +  fileitem.filename
    if os.path.exists(filepath):
        return F, msg+"Already have a file %s. Cannot proceed.\n" % fileitem.filename, ""

    fout = open(filepath, 'wb')
    while 1:
        chunk = fileitem.file.read(100000)
        if not chunk:
            break
        fout.write(chunk)
    fout.close()
    msg += "File '%s' uploaded successfully!\n" % fileitem.filename
    return RSLT_OK, msg, filepath

# this routine is the old-style action API, and is deprecated
def do_put_object(req, obj_type):
    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    result = RSLT_OK
    msg = ""

    # convert form (cgi.fieldStorage) to dictionary
    try:
        obj_name = req.form["name"].value
    except:
        msg += "Error: missing %s name in form data" % obj_type
        req.send_response(req, RSLT_FAIL, msg)
        return

    obj_dict = {}
    for k in list(req.form.keys()):
        obj_dict[k] = req.form[k].value

    # sanity check the submitted data
    for field in required_put_fields[obj_type]:
        try:
            value = obj_dict[field]
        except:
            result = RSLT_FAIL
            msg += "Error: missing required field '%s' in form data" % field
            break

        # FIXTHIS - for cross references (board, resource), check that these
        # are registered with the server
        # here is an example:
        # see if a referenced board is registered with the server
        #if field.startswith("board") or field.endswith("board"):
        #    board_filename = "board-%s.json" % (value)
        #    board_data_dir = req.config.data_dir + os.sep + "boards"
        #    board_path = board_data_dir + os.sep + board_filename
        #
        #    if not os.path.isfile(board_path):
        #        result = RSLT_FAIL
        #        msg += "Error: No matching board '%s' registered with server (from field '%s')" % (value, field)
        #        break

    if result != RSLT_OK:
        req.send_response(req, result, msg)
        return

    req_result = None
    if obj_type == "request":
        obj_dict["state"] = "pending"
        timestamp = get_timestamp()
        obj_name += "-" + timestamp
        obj_dict["name"] = obj_name

    filename = obj_type + "-" + obj_name
    jfilepath = data_dir + os.sep + filename + ".json"

    # convert to json and save to file
    data = json.dumps(obj_dict, sort_keys=True, indent=4,
            separators=(',', ': '))
    fout = open(jfilepath, "w")
    fout.write(data+'\n')
    fout.close()

    msg += "%s accepted (filename=%s)\n" % (obj_name, filename)

    if obj_type == "request":
        req_result, req_msg = process_request(req, obj_dict)
        msg += "%s\n%s" % (req_result, req_msg)

    req.send_response(req, result, msg)

# define an array with the fields that allowed to be modified
# for each different object type:
allowed_update_fields = {
    "board": ["state", "kernel_version", "AssignedTo"],
    "request": ["state", "start_time", "done_time"],
    "resource": ["state", "AssignedTo", "command"]
    }

# Update board, resource and request objects
def do_update_object(req, obj_type):
    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    msg = ""

    try:
        obj_name = req.form[obj_type].value
    except:
        msg += "Error: can't read %s from form" % obj_type
        req.send_response(RSLT_FAIL, msg)
        return

    filename = obj_type + "-" + obj_name + ".json"
    filepath = data_dir + os.sep + filename
    if not os.path.exists(filepath):
        msg += "Error: filepath %s does not exist" % filepath
        req.send_response(RSLT_FAIL, msg)
        return

    # read requested object file
    fd = open(filepath, "r")
    obj_dict = json.load(fd)
    fd.close()

    # update fields from (cgi.fieldStorage)
    for k in list(req.form.keys()):
        if k in [obj_type, "action"]:
            # skip these
            continue
        if k in allowed_update_fields[obj_type]:
            # FIXTHIS - could check the data input here
            obj_dict[k] = req.form[k].value
        else:
            msg = "Error - can't change field '%s' in %s %s (not allowed)" % \
                    (k, obj_type, obj_name)
            req.send_response(RSLT_FAIL, msg)
            return

    # put dictionary back in json format (beautified)
    data = json.dumps(obj_dict, sort_keys=True, indent=4,
            separators=(',', ': '))
    fout = open(filepath, "w")
    fout.write(data+'\n')
    fout.close()

    req.send_response(RSLT_OK, data)

# try matching with simple wildcards (* at start or end of string)
def item_match(pattern, item):
    if pattern=="*":
        return True
    if pattern==item:
        return True
    if pattern.endswith("*") and \
        pattern[:-1] == item[:len(pattern)-1]:
        return True
    if pattern.startswith("*") and \
        pattern[1:] == item[-(len(pattern)-1):]:
        return True
    return False

def do_query_objects(req):
    try:
        obj_type = req.form["obj_type"].value
    except:
        msg = "Error: can't read object type ('obj_type') from form"
        req.send_response(RSLT_FAIL, msg)
        return

    if obj_type not in ["board", "resource", "request"]:
        msg = "Error: unsupported object type '%s' for query" % obj_type
        req.send_response(RSLT_FAIL, msg)
        return

    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    msg = ""

    filelist = os.listdir(data_dir)
    filelist.sort()

    # can query by different fields
    # obj_name is in the filename so we don't need to open the json file
    #   in order to filter by it.
    # other fields are inside the json and requiring opening each file

    try:
        query_obj_name = req.form["name"].value
    except:
        query_obj_name = "*"

    # handle name-based queries
    match_list = []
    for f in filelist:
        prefix = obj_type + "-"
        if f.startswith(obj_type + "-") and f.endswith(".json"):
            file_obj_name = f[len(prefix):-5]
            if not file_obj_name:
                continue
            if not item_match(query_obj_name, file_obj_name):
                continue
            match_list.append(file_obj_name)

    # FIXTHIS - read files and filter by attributes
    # particularly filter on 'state'

    for obj_name in match_list:
       msg += obj_name+"\n"

    req.send_response(RSLT_OK, msg)


def old_do_query_requests(req):
    #log_this("in do_query_requests")
    req_data_dir = req.config.data_dir + os.sep + "requests"
    msg = ""

    filelist = os.listdir(req_data_dir)
    filelist.sort()

    # can query by different fields, some in the name and some inside
    # the json

    try:
        query_host = req.form["host"].value
    except:
        query_host = "*"

    try:
        query_board = req.form["board"].value
    except:
        query_board = "*"

    # handle host and board-based queries
    match_list = []
    for f in filelist:
        if f.startswith("request-") and f.endswith("json"):
            host_and_board = f[31:-5]
            if not host_and_board:
                continue
            if not item_match(query_host, host_and_board.split(":")[0]):
                continue
            if not item_match(query_board, host_and_board.split(":")[1]):
                continue
            match_list.append(f)

    # read files and filter by attributes
    # (particularly filter on 'state')
    if match_list:
        # read the first file to get the list of possible attributes
        f = match_list[0]
        with open(req_data_dir + os.sep + f) as jfd:
            data = json.load(jfd)
            # get a list of valid attributes
            fields = list(data.keys())

            # get rid of fields already processed
            fields.remove("host")
            fields.remove("board")

        # check the form for query attributes
        # if they have the same name as a valid field, then add to list
        query_fields={}
        for field in fields:
            try:
                query_fields[field] = req.form[field].value
            except:
                pass

        # if more to query by, then go through files, preserving matches
        if query_fields:
            ml_tmp = []
            for f in match_list:
                drop = False
                with open(req_data_dir + os.sep + f) as jfd:
                    data = json.load(jfd)
                    for field, pattern in list(query_fields.items()):
                        if not item_match(pattern, str(data[field])):
                            drop = True
                if not drop:
                    ml_tmp.append(f)
            match_list = ml_tmp

    for f in match_list:
        # remove .json extension from request filename, to get the req_id
        req_id = f[:-5]
        msg += req_id+"\n"

    req.send_response(RSLT_OK, msg)

# FIXTHIS - could do get_next_request (with wildcards) to save a query
def do_get_request(req):
    req_data_dir = req.config.data_dir + os.sep + "requests"
    msg = ""

    # handle host and target-based queries
    msg += "In lcserver.py:get_request\n"
    try:
        request_id = req.form["request_id"].value
    except:
        msg += "Error: can't read request_id from form"
        req.send_response(RSLT_FAIL, msg)
        return

    filename = request_id + ".json"
    filepath = req_data_dir + os.sep + filename
    if not os.path.exists(filepath):
        msg += "Error: filepath %s does not exist" % filepath
        req.send_response(RSLT_FAIL, msg)
        return

    # read requested file
    request_fd = open(filepath, "r")
    mydict = json.load(request_fd)

    # beautify the data, for now
    data = json.dumps(mydict, sort_keys=True, indent=4, separators=(',', ': '))
    req.send_response(RSLT_OK, data)

def do_remove_object(req, obj_type):
    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    msg = ""

    try:
        obj_name = req.form[obj_type].value
    except:
        msg += "Error: can't read '%s' from form" % obj_type
        req.send_response(RSLT_FAIL, msg)
        return

    filename = obj_name + ".json"
    filepath = data_dir + os.sep + filename
    if not os.path.exists(filepath):
        msg += "Error: filepath %s does not exist" % filepath
        req.send_response(RSLT_FAIL, msg)
        return

    # FIXTHIS - should check permissions here
    # only original-submitter and resource-host are allowed to remove
    os.remove(filepath)

    msg += "%s %s was removed" % (obj_type, obj_name)
    req.send_response(RSLT_OK, msg)

def file_list_html(req, file_type, subdir, extension):
    if file_type == "files":
        src_dir = req.config.files_dir + os.sep + subdir
    elif file_type == "data":
        src_dir = req.config.data_dir + os.sep + subdir
    elif file_type == "page":
        src_dir = req.config.page_dir
    else:
        raise ValueError("cannot list files for file_type %s" % file_type)

    full_dirlist = os.listdir(src_dir)
    full_dirlist.sort()

    # filter list to only ones with requested extension
    filelist = []
    for d in full_dirlist:
        if d.endswith(extension):
            filelist.append(d)

    if not filelist:
        return req.html_error("No %s (%s) files found." % (subdir[:-1], extension))

    files_url = "%s/%s/%s/" % (config.files_url_base, file_type, subdir)
    html = "<ul>"
    for item in filelist:
        html += '<li><a href="'+files_url+item+'">' + item + '</a></li>\n'
    html += "</ul>"
    return html

def show_request_table(req):
    src_dir = req.config.data_dir + os.sep + "requests"

    full_dirlist = os.listdir(src_dir)
    full_dirlist.sort()

    # filter list to only request....json files
    filelist = []
    for f in full_dirlist:
        if f.startswith("request") and f.endswith(".json"):
            filelist.append(f)

    if not filelist:
        return req.html_error("No request files found.")

    files_url = config.files_url_base + "/data/requests/"
    html = """<table border="1" cellpadding="2">
  <tr>
    <th>Request</th>
    <th>State</th>
    <th>Requestor</th>
    <th>Host</th>
    <th>Board</th>
    <th>Test</th>
    <th>Run (results)</th>
  </tr>
"""
    for item in filelist:
        request_fd = open(src_dir+os.sep + item, "r")
        req_dict = json.load(request_fd)
        request_fd.close()

        # add data, in case it's missing
        try:
            run_id = req_dict["run_id"]
        except:
            req_dict["run_id"] = "Not available"

        html += '  <tr>\n'
        html += '    <td><a href="'+files_url+item+'">' + item + '</a></td>\n'
        for attr in ["state", "requestor", "host", "board", "test_name",
                "run_id"]:
            html += '    <td>%s</td>\n' % req_dict[attr]
        html += '  </tr>\n'
    html += "</table>"
    req.html.append(html)

# returns proc-data, which is a dictionary with:
# key='webterm-{board}', attr=dictionary with:
#   keys 'pid' and 'port'
def read_proc_data(req):
    file_path = "%s/proc-data.json" %  (req.config.base_dir)
    try:
        fd = open(file_path)
        proc_data = json.load(fd)
        fd.close()
    except (FileNotFoundError, PermissionError):
        log_this("Error reading proc_data")
        proc_data = {}
    except (ValueError, json.JSONDecodeError):
        log_this("Cannot parse proc_data as json")
        proc_data = {}

    return proc_data

def write_proc_data(req, proc_data):
    file_path = "%s/proc-data.json" %  (req.config.base_dir)
    try:
        fd = open(file_path, "w")
        json.dump(proc_data, fd, indent=4)
        fd.close()
    except:
        log_this("Error writing proc_data")

# Possible solution to the port re-use problem:
# We could add port use memory: put used ports in a bucket, and avoid using
# them for some period of time. (e.g. by adding a delay before using a
# port in the bucket, and having them expire from the bucket over time)
#
def find_unused_port(proc_data):
    # FIXTHIS - the current algorithm leads to rapid port re-use
    # which might be a security issue.

    # try ports in range 7681 to 7781
    for port in range(7681, 7781):
        # find out if this port is unused
        used = False
        for proc in proc_data.values():
            if port == proc["port"]:
                used = True
                break
        if not used:
            break

    if not used:
        log_this("Could not find free port for webterminal process start")

    return port

# starts webterm process, if needed.
# returns (port, msg)
# on success msg is empty, and port has the port where the web terminal
# can be accessed
# on failure, msg has a string indicating the problem
def start_webterm_process(req, board):
    proc_data = read_proc_data(req)
    msg = ""

    pd_key = "webterm-%s" % board
    if pd_key in proc_data:
        port = proc_data[pd_key]["port"]
    else:
        # start process
        port = find_unused_port(proc_data)
        wt_cmd="/usr/local/bin/ttyd -p %d %%(login_cmd)s" % port

        bmap = get_object_map(req, "board", board)
        iwt_cmd = get_interpolated_str(wt_cmd, bmap)

        (pid, msg) = start_command(req, pd_key, iwt_cmd)
        if pid:
            proc_data[pd_key] = { "port": port, "pid": pid }
            write_proc_data(req, proc_data)
        else:
            msg = "Error starting ttyd process for web terminal support:\n" + msg
            port = 0

    return (port, msg)

# return non-empty message string on error, otherwise None on success
def stop_webterm_process(req, board):
    proc_data = read_proc_data(req)
    pd_key = "webterm-%s" % board

    try:
        pid = proc_data[pd_key]["pid"]
    except KeyError:
        msg = "Error: Could not find pid for process '%s'" % pd_key
        log_this(msg)
        return msg

    os.kill(pid, signal.SIGTERM)

    # remove data files
    cout_file = "/tmp/capture-stdout-" + pd_key
    if os.path.exists(cout_file):
        dlog_this("Removing webterm stdout file %s" % cout_file)
        os.remove(cout_file)

    cerr_file = "/tmp/capture-stderr-" + pd_key
    msg = ""
    if os.path.exists(cerr_file):
        stderr = open(cerr_file, "r").read()
        if stderr:
            msg = "stderr output from webterm command: '%s'" % stderr
            log_this(msg)
        dlog_this("Removing webterm stderr file %s" % cerr_file)
        os.remove(cerr_file)

    # Finally, remove the entry from the process database
    del(proc_data[pd_key])
    write_proc_data(req, proc_data)

    return msg


def show_web_terminal(req, board):
    #check board reservation
    bmap = get_object_map(req, "board", board)
    operation = "webterm show"
    if not user_has_board_reserved(req, bmap, operation, False):
        msg = "Error: You do not have board '%s' reserved." % board
        req.html.append(req.html_error(msg))
        board_link = req.config.url_base + "/boards/" + board
        req.html.append("""<p><a href="%s">Back to board '%s' page</a>""" % (board_link, board))
        return

    # start webterm process, if it's not already running
    port, msg = start_webterm_process(req, board)

    if msg:
        # report error starting webterm
        frame_html = """<table width="640" height="480"><tr><td>%s</td></tr></table>""" % req.html_error(msg)
    else:
        webterm_url = req.config.url_prefix + "/webterm/%s" % port
        frame_html = """<iframe src="%s" width="640" height="480"></iframe>""" % webterm_url

    html = """<table border="1" cellpadding="2">
  <tr>
  <td>%s</td>
  <td>%s</td>
  </tr>
""" % (board, frame_html)
    html += "</table>"
    req.html.append(html)

    # FIXTHIS - show a link that stops the webterm
    webterm_stop_button = req.config.url_base + "/api/v0.2/devices/%s/webterm/stop" % bmap["name"]
    req.html.append("""
<p><form method="get" action="%s">
<input type="submit" name="button" value="Stop Web Terminal">
</form>
""" % webterm_stop_button)

    board_link = req.config.url_base + "/boards/" + board
    req.html.append("""<p><a href="%s">Back to board '%s' page</a>""" % (board_link, board))

# NOTE: we're inside a table cell here
def show_board_info(req, bmap):
    # list of connected resources
    req.html.append("<h3>Resources</h3>\n<ul>")
    pc = bmap.get("power_controller", "")
    cm = bmap.get("camera", "")
    lc = bmap.get("login_cmd", "")

    resource_shown = False
    if pc:
        req.html.append("<li>Power controller: %s</li>\n" % pc)
        resource_shown = True
    if cm:
        req.html.append("<li>Camera module: %s</li>\n" % cm)
        resource_shown = True
    if not resource_shown:
        req.html.append("<li><i>No connected resources found!</i></li>\n")
    req.html.append("</ul>\n")

    req.html.append("<h3>Status</h3>\n<ul>")

    reservation = bmap.get("AssignedTo", "None")
    req.html.append("<li>Reservation: %s</li>" % reservation)

    # show power status
    if pc:
       (result, msg) = get_power_status(req, bmap)
       if result == RSLT_OK:
           power_status = msg
       else:
           power_status = req.html_error(msg)
    else:
       power_status = "Unknown"
    req.html.append("<li>Power Status: %s</li>\n" % power_status)
    (net_status_result, net_status_msg) = get_network_status(req, bmap)
    (cmd_status_result, cmd_status_msg) = get_command_status(req, bmap)
    if net_status_result == RSLT_OK:
        network_status = net_status_msg
    else:
        network_status = req.html_error(net_status_msg)
    if cmd_status_result == RSLT_OK:
        command_status = cmd_status_msg
    else:
        command_status = req.html_error(cmd_status_msg)

    req.html.append("<li>Network Status: %s</li>\n" % network_status)
    req.html.append("<li>Command Status: %s</li>\n" % command_status)

    req.html.append("</ul>\n")
    req.html.append("<h3>Actions</h3>\n<ul>\n")
    if pc:
        reboot_link = req.config.url_base + "/api/v0.2/devices/%s/power/reboot" % (bmap["name"])
        req.html.append("""
<form method="get" action="%s">
<input type="submit" name="button" value="Reboot">
</form>
""" % reboot_link)
        on_link = req.config.url_base + "/api/v0.2/devices/%s/power/on" % (bmap["name"])
        req.html.append("""
<form method="get" action="%s">
<input type="submit" name="button" value="ON">
</form>
""" % on_link)
        off_link = req.config.url_base + "/api/v0.2/devices/%s/power/off" % (bmap["name"])
        req.html.append("""
<form method="get" action="%s">
<input type="submit" name="button" value="OFF">
</form>
""" % off_link)

    # show the camera action links
    if cm:
        image_link = req.config.url_base + "/api/v0.2/devices/%s/camera/image" % (bmap["name"])
        req.html.append("""
<form method="get" action="%s">
<input type="submit" name="button" value="Capture Image">
</form>
""" % image_link)

        video_link = req.config.url_base + "/api/v0.2/devices/%s/camera/video" % (bmap["name"])
        req.html.append("""
<form method="get" action="%s">
<input type="submit" name="button" value="Capture Video">
</form>
""" % video_link)

        live_stream_start_link = req.config.url_base + "/api/v0.2/devices/%s/camera/live_stream_start" % bmap["name"]
        req.html.append("""
<form method="get" action="%s">
<input type="submit" name="button" value="Start LiveStream">
</form>
""" % live_stream_start_link)

        live_stream_stop_link = req.config.url_base + "/api/v0.2/devices/%s/camera/live_stream_stop" % bmap["name"]
        req.html.append("""
<form method="get" action="%s">
<input type="submit" name="button" value="Stop LiveStream">
</form>
""" % live_stream_stop_link)

    if lc:
        webterm_link = req.config.url_base + "/webterm/%s" % (bmap["name"])
        req.html.append("""
        <a href="%s">Web terminal</a>
""" % webterm_link)

        webterm_stop_button = req.config.url_base + "/api/v0.2/devices/%s/webterm/stop" % bmap["name"]
        req.html.append("""
<form method="get" action="%s">
<input type="submit" name="button" value="Stop Web Terminal">
</form>
""" % webterm_stop_button)

    req.html.append("</ul>")


def show_object(req, obj_type, obj_name):
    if obj_type == "board":
        show_board(req, obj_name)
    elif obj_type == "resource":
        show_resource(req, obj_name)
    else:
        title = "Error - object type '%s'" % req.obj_type
        req.add_to_message(title)


# returns (RSLT_OK, status|RSLT_FAIL, message)
# status can be one of: "ON", "OFF", "UNKNOWN"
def get_power_status(req, bmap):
    pdu_map = get_connected_resource(req, bmap, "power_controller")
    if not pdu_map:
        msg = "Board %s has no connected power_controller resource" % bmap["name"]
        return (RSLT_FAIL, msg)

    # lookup command to execute in resource_map
    if "status_cmd" not in pdu_map:
        msg = "Resource '%s' does not have status_cmd attribute, cannot execute" % pdu_map["name"]
        return (RSLT_FAIL, msg)

    cmd_str = pdu_map["status_cmd"]
    icmd_str = get_interpolated_str(cmd_str, bmap, pdu_map)

    rcode, output = lc_getstatusoutput(req, icmd_str)
    if rcode:
        msg = "Result of power status operation on board %s = %d\n" % (bmap["name"], rcode)
        msg += "command output='%s'" % output
        return (RSLT_FAIL, msg)

    # FIXTHIS - translate power status output here, if needed

    status_str = output.strip()
    return (RSLT_OK, status_str)

 # returns (RSLT_OK, status|RSLT_FAIL, message)
 # status can be one of: "RESPONSIVE", "NONRESPONSIVE", "UNKNOWN"
def get_network_status(req, bmap):
    # lookup command to execute in board_map
    if "network_status_cmd" not in bmap:
        msg = "board '%s' does not have network_status_cmd attribute, cannot execute" % bmap["name"]
        return (RSLT_FAIL, msg)

    cmd_str = bmap["network_status_cmd"]
    icmd_str = get_interpolated_str(cmd_str, bmap)
    rcode, output = lc_getstatusoutput(req, icmd_str)
    if rcode:
        msg = "Result of network status operation on board %s = %d\n" % (bmap["name"], rcode)
        msg += "command output='%s'" % output
        return (RSLT_FAIL, msg)
    status_str = output.strip()
    if status_str not in ["RESPONSIVE", "NONRESPONSIVE", "UNKNOWN"]:
        log_this("Invalid status_str of '%s' received from network_status_cmd" % status_str)
    return (RSLT_OK, status_str)

 # returns (RSLT_OK, status|RSLT_FAIL, message)
 # status can be one of: "OPERATIVE", "INPORATIVE", "UNKNOWN"
def get_command_status(req,bmap):
    # lookup command to execute in board_map
    if "command_status_cmd" not in bmap:
        msg = "board '%s' does not have command_status_cmd attribute, cannot execute" % bmap["name"]
        return (RSLT_FAIL, msg)
    cmd_str = bmap["command_status_cmd"]
    icmd_str = get_interpolated_str(cmd_str, bmap)
    rcode, output = lc_getstatusoutput(req, icmd_str)
    status_str = output.strip()
    if status_str not in ["OPERATIVE", "INOPERATIVE", "UNKNOWN"]:
        log_this("Invalid status_str of '%s' received from command_status_cmd" % status_str)
    return (RSLT_OK, status_str)

# show the web ui for boards on this machine
def show_boards(req):
    boards = get_object_list(req, "board")

    # show a table of attributes
    req.html.append('<table class="boards_table">\n<tr>\n')
    req.html.append("  <th>Name</th><th>Description</th>\n</tr>\n")
    for board in boards:
        req.html.append("<tr>\n")
        bmap = get_object_map(req, "board", board)
        description = bmap.get("description", "")
        board_link = req.config.url_base + "/boards/" + board

        req.html.append('  <td valign="top" align="center"><b><a href="%s">%s</a></b></td>\n' % (board_link, board))
        req.html.append('  <td valign="top">%s</td>\n' % description)
        req.html.append("</tr>\n")

    req.html.append("</table>")

def show_board(req, board):
    req.html.append("<H1>Board %s</h1>" % board)
    bmap = get_object_map(req, "board", board)

    req.html.append('<table class="board_table">\n<tr>\n')
    # create message area for board operations
    # should I do a popup instead?
    req.html.append("""
    <td style="padding: 10px" colspan="3">
       <div id="message" width="100%" height="60px" margin="5px">
       <i>... Message area ...</i>
       </div>
    </td></tr><tr>
    """)
    req.html.append('  <td style="padding: 10px" valign="top">')
    show_board_info(req, bmap)
    req.html.append('  </td><td style="padding: 5px" valign="top" width="300px">')

    # show some more stuff:

    #cmd_str = bmap.get("run_cmd", None)
    #if cmd_str:
    #    run_map = { "command": "uptime" }
    #
    #    icmd_str = get_interpolated_str(cmd_str, bmap, run_map)
    #    rcode, output = lc_getstatusoutput(req, icmd_str)
    #else:
    #    output = "<i>Board does not have a 'run_cmd' specified</i>"
    #    rcode = 0
    #
    #if rcode:
    #    output = "<i>problem getting uptime</i>"
    #req.html.append("<b>Uptime:</b> %s<BR>" % output.strip())

    # use javascript to get the uptime for the board
    # here's where the uptime info will land, and the button
    req.html.append("""<h3>Uptime</h3>
       <div id="uptime" width="100%" height="60px" margin="5px">
       Not obtained yet....
       </div><br>
       <button type="button" onclick="getUptime()">Get Uptime</button>
    <br>""" )

    run_url = req.config.url_base + os.sep + "api/v0.2/devices/%s/run/" % board

    # here's the script for the button to get the latest uptime
    req.html.append("""<script>
function getUptime() {
  var xhttp = new XMLHttpRequest();
  var url = "%s";
  var params = "command=uptime&submit_button=Run";

  document.getElementById("uptime").innerHTML = "<i>Getting uptime...</i>";

  xhttp.open("POST", url, true);
  xhttp.setRequestHeader('Content-type', 'application/x-www-form-urlencoded');

  xhttp.onreadystatechange = function () {
    if (this.readyState == 4 && this.status == 200) {
       result = JSON.parse(this.responseText)
       if (result.result == "fail") {
          document.getElementById("uptime").innerHTML = "<font color=red>Error: " + result.message + "</font>";
       } else {
           uptime = result.data.data;
           document.getElementById("uptime").innerHTML = uptime;
       }
    }
  };

  xhttp.send(params);
}
</script>
""" % run_url)

    # show a form to execute a command on the board
    req.html.append("<hr><p>")
    req.html.append("Command to execute on board:")

    req.html.append("""<FORM METHOD="POST" ACTION="%s">
    <INPUT type="text" name="command" width=50></input><BR>
    <INPUT type="submit" name="submit_button" value="Run"></input>
    </FORM>""" % run_url)

    # show reservation/release buttons

    reserve_link=req.config.url_base + "/api/v0.2/devices/%s/assign" % board
    release_link=req.config.url_base + "/api/v0.2/devices/%s/release" % board
    #req.html.append("</td>")
    force_link=req.config.url_base + "/api/v0.2/devices/%s/release/force" % board

    reserve_form_disabled_str = """<FORM METHOD="GET" ACTION="%s" id="ReserveForm">
        <button type="button" disabled>Reserve Board</button>
        </FORM>""" % reserve_link
    release_form_disabled_str = """<FORM METHOD="GET" ACTION="%s" id="ReleaseForm">
        <button type="button" disabled>Release</button>
        </FORM>""" % release_link
    force_form_disabled_str = """<FORM METHOD="GET" ACTION="%s" id="ForceReleaseForm">
        <button type="button" disabled>Force Release!</button>
        </FORM>""" % force_link


    user = req.get_user()
    log_this("new_user-%s" % user)
    assigned_to = bmap.get("AssignedTo", "nobody")
    req.html.append("<hr>")
    if assigned_to == "nobody":
        # nobody has it, I can allocate it
        def_duration = req.config.default_reservation_duration
        if def_duration == "forever":
            dur_str = "forever"
        else:
            dur_str = "%s minutes" % def_duration

        req.html.append("""Enter time in minutes to reserve the board
                           example: 30 or 60 or 120 etc..
                           If the time is not specified, the duration will be
                           %s""" % dur_str)

        req.html.append("""<FORM METHOD="GET" ACTION="%s" id="ReserveForm">
        <INPUT type="text" name="duration" width=50></input><BR>
        <INPUT type="submit" name="submit_button" onClick="DoReservationOperation(this.form)" value="Reserve Board"></input>
        </FORM>""" % reserve_link)
        req.html.append(release_form_disabled_str)
        req.html.append(force_form_disabled_str)

    if assigned_to == user:
        # I have it, I can release it
        req.html.append("""Board is currently reserved by %s """ % user)
        req.html.append(reserve_form_disabled_str)
        req.html.append("""Press the button to release the board""")
        req.html.append("""<FORM METHOD="GET" ACTION="%s" id="ReleaseForm">
        <INPUT type="submit" name="submit_button" onClick="DoReservationOperation(this.form)" value="Release"></input>
        </FORM>""" % release_link)
        req.html.append(force_form_disabled_str)

    if assigned_to != user and assigned_to != "nobody":
        # I don't have it, I can force-release it
        req.html.append("""Board is not assigned to you.
                           It is assigned to %s""" % assigned_to)

        req.html.append(reserve_form_disabled_str)
        req.html.append(release_form_disabled_str)
        req.html.append("Press the button to release the board forcefully")
        req.html.append("""<FORM METHOD="GET" ACTION="%s" id="ForceReleaseForm">
        <INPUT type="submit" name="submit_button" onClick="DoReservationOperation(this.form)" value="Force Release!"></input>
        </FORM>""" % force_link)

    upload_link = req.config.url_base + "/api/v0.2/devices/%s/upload" % bmap["name"]
    req.html.append("<hr><p>")
    req.html.append("Select a file and destination path, to upload a file to the target board.<br>")
    req.html.append("""<FORM METHOD="POST" ACTION="%s" enctype="multipart/form-data">
         File: <INPUT type="file" name="file"></input></br>
         Dest. path: <INPUT type="text" name="path" placeholder="/tmp" value="/tmp" width=300></input><BR>
         <INPUT type="submit" name="submit_button" value="Upload"></input>
         </FORM>""" % upload_link)

    req.html.append("</td>")

    # FIXTHIS - use javascript to put run result into a box, instead
    # of switching to a new page with a form submission

    req.html.append("""<script>

function myPreventDefault(event) {
    event.preventDefault();
}

document.getElementById('ReserveForm').addEventListener("submit", myPreventDefault);
document.getElementById('ReleaseForm').addEventListener("submit", myPreventDefault);
document.getElementById('ForceReleaseForm').addEventListener("submit", myPreventDefault);

function DoReservationOperation(form) {
  var xhttp = new XMLHttpRequest();
  var url = form.action;
  var duration = "";
  var params = "";
  var msg = "";
  var method = "GET";

  if (form.id == "ReserveForm") {
      duration = form.duration.value;
      params = "duration=" + duration;
      msg = "<i>Reserving Board...</i>";
      method = "POST";
  }
  if (form.id == "ReleaseForm") {
      msg = "<i>Releasing Board...</i>";
  }
  if (form.id == "ForceReleaseForm") {
      msg = "<i>Releasing Board (forcefully)...</i>";
  }

  document.getElementById("message").innerHTML = msg;

  xhttp.open(method, url, true);
  xhttp.setRequestHeader('Content-type', 'application/x-www-form-urlencoded');

  xhttp.onreadystatechange = function () {
    if (this.readyState == 4 && this.status == 200) {
       result = JSON.parse(this.responseText)
       msg = result.message;
       if (result.result == "fail") {
          msg = "<font color=red>Error:" + msg + "</font>";
       }
       document.getElementById("message").innerHTML = msg;
       alert(msg);
       window.location.reload(true);
    }
  };

  xhttp.send(params);
}
</script>
""")

    # show last image taken by camera, if there is one

    image_link_filename = "%s-last-camera-image.jpeg" % board
    image_link_path = req.config.files_dir + "/" + image_link_filename
    # note: link contents (target) is just the filename
    image_url = ""
    if os.path.islink(image_link_path):
        image_filename = os.readlink(image_link_path)
        if os.path.isfile(req.config.files_dir + "/" + image_filename):
            image_url = req.config.files_url_base + "/files/" + image_filename

    video_link_filename = "/%s-last-camera-video.mp4" % board
    video_link_path = req.config.files_dir + video_link_filename
    # note: link contents (target) is just the filename
    video_url = ""
    if os.path.islink(video_link_path):
        video_filename = os.readlink(video_link_path)
        if os.path.isfile(req.config.files_dir + "/" + video_filename):
            video_url = req.config.files_url_base + "/files/" + video_filename

    if image_url or video_url:
        req.html.append('<td style="padding: 5px" align=top>\n')
        if image_url:
            req.html.append("""
<h2 align="center">Last Camera Image</h2>
<image src="%s" height="200" width="300">
<p>
""" % image_url)

        if video_url:
            req.html.append("""
<h2 align="center">Last Camera Video</h2>
<video width="400" controls>
   <source src="%s" type="video/mp4">
   Your browser does not support HTML video.
</video>
""" % video_url)

    req.html.append("</td></tr></table>\n")


def show_resources(req):
    resources = get_object_list(req, "resource")

    # show a table of attributes
    req.html.append('<table class="resources_table">\n<tr>\n')
    req.html.append("  <th>Name</th><th>Description</th>\n</tr>\n")
    for resource in resources:
        req.html.append("<tr>\n")
        rmap = get_object_map(req, "resource", resource)
        res_link = req.config.url_base + "/resources/" + resource

        req.html.append('  <td valign="top" align="center"><b><a href="%s">%s</a></b></td>\n' % (res_link, resource))
        description = rmap.get("description",
                req.html_error("No description available"))
        req.html.append('  <td valign="top">%s</td>\n' % description)
        req.html.append("</tr>\n")

    req.html.append("</table>")

def show_resource(req, resource):
    req.html.append("<H1>Resource %s</h1>" % resource)
    rmap = get_object_map(req, "resource", resource)

    req.html.append('<table class="resource_table">\n<tr>\n')
    req.html.append('  <td style="padding: 10px">')

    # show attributes here
    # split them into two sets: attributes and commands
    rattr_keys = []
    rcmd_keys = []
    for key in list(rmap.keys()):
        if key.endswith("_cmd"):
            rcmd_keys.append(key)
        else:
            rattr_keys.append(key)

    dlog_this("rattr_keys=%s" % str(rattr_keys))

    rattr_keys.sort()
    req.html.append("<h3>Attributes</h3>")
    req.html.append("<ul>")
    for name in rattr_keys:
        req.html.append("<li><b>%s</b>: %s</li>" % (name, rmap[name]))
    req.html.append("</ul>")

    rcmd_keys.sort()
    req.html.append("<h3>Commands</h3>")
    req.html.append("<ul>")
    for name in rcmd_keys:
        req.html.append("<li><b>%s</b>: %s</li>" % (name, rmap[name]))
    req.html.append("</ul>")

    req.html.append("</td></tr></table>\n")

def show_users(req):
    d = {}
    users = get_object_list(req, "user")
    boards = get_object_list(req, "board")

    # collect reservation data
    # each entry is res_map is a list of reservations for a given user user
    # consisting of (board, start_time, end_time)
    res_map = {}
    for board in boards:
        bmap = get_object_map(req, "board", board)
        res_user = bmap["AssignedTo"]
        if res_user != "nobody":
            start_time = bmap.get("start_time", "unknown")
            end_time = bmap.get("end_time", "unknown")
            reservation = (board, start_time, end_time)

            if res_user not in res_map:
                res_map[res_user] = [reservation]
            else:
                res_map[res_user].append(reservation)

    # show a table of attributes
    req.html.append('<table class="user_table">\n<tr>\n')
    req.html.append('  <th>Name</th><th>Reservations</th><th>Last access</th>\n</tr>\n')
    for user in users:
        req.html.append("<tr>\n")
        umap = get_object_map(req, "user", user)
        reservations = res_map.get(user, [])

        req.html.append('  <td valign="top" align="center"><h3>%s</h3></td>\n' % umap["name"])

        if reservations:
            req.html.append("""    <td><table>
    <tr><th>Board</th><th>Start Time</th><th>End Time</th></tr>""")
            for res in reservations:
                req.html.append('    <tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (res[0], res[1], res[2]))
            req.html.append("    </table>\n  </td>")
        else:
            req.html.append('  <td valign="top"><i>None</i></td>')

        req.html.append(""" <td valign="top"><i>Not implemented yet</i></td>\n""")

        req.html.append("</tr>\n")

    req.html.append("</table>")

# show the web ui for objects on the server
# this is the main human interface to the server
def do_show(req):
    page_name = req.page_name
    dlog_this("in do_show, page_name='%s'\n" % page_name)

    handled = False
    if page_name in ["boards", "users", "resources", "requests", "logs"]:
        show_header(req, "Lab Control %s%s" % (page_name[0].upper(), page_name[1:]))

    if page_name=="boards":
        show_boards(req)
        handled = True
    if page_name == "users":
        show_users(req)
        handled = True
    if page_name == "resources":
        show_resources(req)
        handled = True
    if page_name == "requests":
        req.html.append("<H1>Table of requests</H1>")
        show_request_table(req)
        handled = True
    if page_name == "logs":
        req.html.append("<H1>Table of logs</H1>")
        req.html.append(file_list_html(req, "files", "logs", ".txt"))
        handled = True

    admin_pages = ["Admin"]

    # check for a page from the page directory here
    if os.path.isfile(req.page_filename()):
        if not req.user.admin and page_name in admin_pages:
            msg = "Error: access to page '%s' requires Administrator privileges (which you do not have)" % page_name
            show_header(req, "Permission Failure")
            req.html.append(req.html_error(msg))
            req.show_footer()
            return

        raw_data = req.read_page()
        # interpolate data into the page
        data = raw_data % req.data
        show_header(req, "")
        req.html.append(data)
        handled = True

    if not handled:
        # check for object name here, and show individual object
        #   status and control interface
        if req.obj_type in ["board", "resource"]:
            show_header(req, "Lab Control %s" % req.obj_type)
            # show individual object
            show_object(req, req.obj_type, req.page_name)
        elif req.obj_type == "webterm":
            show_header(req, "Lab Control Web Terminal")
            show_web_terminal(req, req.page_name)
        else:
            show_header(req, "Lab Control")
            title = "Error - unsupported object type '%s'" % req.obj_type
            req.add_to_message(title)

    # print a divider between page data and the object menus
    if req.page_name != "Main":
        req.html.append("<br><hr>")

    req.html.append("""<a href="%(url_base)s">Back to home page</a>""" % req.config)

    req.show_footer()

# show raw objects
def do_raw(req):
    show_header(req, "Lab Control Raw objects")
    dlog_this("in do_raw, req.page_name='%s'\n" % req.page_name)
    #req.html.append("req.page_name='%s' <br><br>" % req.page_name)

    if req.page_name not in ["boards", "resources", "users", "requests", "logs", "main"]:
        title = "Error - unknown object type '%s'" % req.page_name
        req.add_to_message(title)
    else:
        if req.page_name=="boards":
            req.html.append("<H1>List of boards</h1>")
            req.html.append(file_list_html(req, "data", "boards", ".json"))
        elif req.page_name == "resources":
            req.html.append("<H1>List of resources</h1>")
            req.html.append(file_list_html(req, "data", "resources", ".json"))
        elif req.page_name == "users":
            req.html.append("<H1>List of users</h1>")
            req.html.append(file_list_html(req, "data", "users", ".json"))
        elif req.page_name == "requests":
            req.html.append("<H1>Table of requests</H1>")
            show_request_table(req)
        elif req.page_name == "logs":
            req.html.append("<H1>Table of logs</H1>")
            req.html.append(file_list_html(req, "files", "logs", ".txt"))

    if req.page_name != "main":
        req.html.append("<br><hr>")

    req.html.append("<H1>Lab Control raw objects on this server</h1>")
    req.html.append("""
Here are links to the different Lab Control objects:<br>
<ul>
<li><a href="%(url_base)s/raw/boards">Boards</a></li>
<li><a href="%(url_base)s/raw/resources">Resources</a></li>
<li><a href="%(url_base)s/raw/users">Users</a></li>
<li><a href="%(url_base)s/raw/requests">Requests</a></li>
<li><a href="%(url_base)s/raw/logs">Logs</a></li>
</ul>
<hr>
""" % req.config )

    req.html.append("""<a href="%(url_base)s">Back to home page</a>""" % req.config)

    req.show_footer()

# get a list of items of the indicated object type
# (by scanning the data/{obj_type}s directory, and
# parsing the filenames)
# returns a list of strings with the item names
def get_object_list(req, obj_type):
    data_dir = req.config.data_dir + os.sep + obj_type + "s"
    obj_list = []

    filelist = os.listdir(data_dir)
    prefix = obj_type+"-"
    for f in filelist:
        if f.startswith(prefix) and f.endswith(".json"):
            # remove board- and '.json' to get the board_name
            obj_name = f[len(prefix):-5]
            obj_list.append(obj_name)

    obj_list.sort()
    return obj_list

# get a list of resource of a particular type
def get_resource_list_by_type(req, res_type):
    data_dir = req.config.data_dir + "/resources"
    res_list = []

    filelist = os.listdir(data_dir)
    prefix = "resource-"
    for f in filelist:
        if f.startswith(prefix) and f.endswith(".json"):
            # remove board- and '.json' to get the board_name
            name = f[len(prefix):-5]
            rmap = get_object_map(req, "resource", name)
            if res_type in rmap["type"]:
                res_list.append(name)

    res_list.sort()
    return res_list

# supported api actions by path:
# devices = list boards
# devices/{board} = show board data (json file data)
# devices/{board}/status = show board status
# devices/{board}/power/reboot = reboot board
# resources = list resources
# resources/{resource} = show resource data (json file data)

def return_api_object_list(req, obj_type):
    obj_list = get_object_list(req, obj_type)
    req.send_api_list_response(obj_list)

# read data from json file (from data/{obj_type}s/{obj_type}-{name}.json)
# log any errors encountered
def get_object_data(req, obj_type, name):
    filename = obj_type + "-" + name + ".json"
    file_path = "%s/%ss/%s" %  (req.config.data_dir, obj_type, filename)

    if not os.path.isfile(file_path):
        msg = "%s object '%s' is not recognized by the server" % (obj_type, name)
        msg += "- file_path was '%s'" % file_path
        log_this(msg)
        return {}

    data = ""
    try:
        data = open(file_path, "r").read()
    except:
        msg = "Could not retrieve information for %s '%s'" % (obj_type, name)
        msg += "- file_path was '%s'" % file_path
        log_this(msg)
        return {}

    return data

# get object data from file, return api response on error
def get_api_object_data(req, obj_type, obj_name):
    filename = obj_type + "-" + obj_name + ".json"
    file_path = "%s/%ss/%s-%s.json" %  (req.config.data_dir, obj_type, obj_type, obj_name)

    if not os.path.isfile(file_path):
        msg = "%s object '%s' in not recognized by the server" % (obj_type, obj_name)
        msg += "- file_path was '%s'" % file_path
        req.send_api_response_msg(RSLT_FAIL, msg)
        return {}

    data = ""
    try:
        data = open(file_path, "r").read()
    except:
        msg = "Could not retrieve information for %s '%s'" % (obj_type, obj_name)
        msg += "- file_path was '%s'" % file_path
        req.send_api_response_msg(RSLT_FAIL, msg)
        return {}

    return data

# return the list of boards that I have reserved
# (that are assigned to me)
def return_my_board_list(req):
    user = req.get_user()

    if user == "not-logged-in":
        req.send_api_response_msg(RSLT_FAIL, "You are not logged in.")
        return

    boards = get_object_list(req, "board")
    my_boards =  []
    for board in boards:
        board_map = get_object_map(req, "board", board)
        if not board_map:
            continue
        assigned_to = board_map.get("AssignedTo", "nobody")
        if user == assigned_to:
            my_boards.append(board)

    req.send_api_list_response(my_boards)

# return python data structure from json file
#  (from data/{obj_type}s/{obj_type}-{obj_name}.json)
def get_object_map(req, obj_type, obj_name):
    data = get_object_data(req, obj_type, obj_name)
    if not data:
        return {}
    try:
        obj_map = json.loads(data)
    except:
        msg = "Invalid json detected in %s '%s'" % (obj_type, obj_name)
        msg += "\njson='%s'" % data
        log_this(msg)
        return {}

    if obj_type == "board":
        # sythesize the 'board' variable, used by some resource cmds
        obj_map["board"] = obj_map["name"]
        try:
            assigned_to = obj_map["AssignedTo"]
        except KeyError:
            clear_reservation(req, obj_map)
            assigned_to = "nobody"

    # this might cause this to get called too often...
    if obj_type == "board" and assigned_to != "nobody":
        if check_reservation_expiry(req, obj_map):
            # reload the data, it just changed
            # note: recursion is ugly
            return get_object_map(req, obj_type, obj_name)

    return obj_map

# return python data structure from json file
#  (from data/{obj_type}s/{obj_type}-{obj_name}.json)
def get_api_object_map(req, obj_type, obj_name):
    data = get_api_object_data(req, obj_type, obj_name)
    if not data:
        return {}
    try:
        obj_map = json.loads(data)
    except:
        msg = "Invalid json detected in %s '%s'" % (obj_type, obj_name)
        msg += "\njson='%s'" % data

        req.send_api_response_msg(RSLT_FAIL, msg)
        return {}

    # this might cause this to get called too often...
    if obj_type == "board" and obj_map["AssignedTo"] != "nobody":
        if check_reservation_expiry(req, obj_map):
            # reload the data, it just changed
            # note: recursion is ugly
            return get_object_map(req, obj_type, obj_name)

    return obj_map

def save_object_data(req, obj_type, obj_name, obj_data):
    msg = ""

    filename = obj_type + "-" + obj_name + ".json"
    file_path = "%s/%ss/%s" %  (req.config.data_dir, obj_type, filename)

    # remove synthesized 'board' attribute
    if obj_type == "board":
        del obj_data["board"]

    #log_this("in save_object_data: obj_data=%s" % obj_data)

    json_data = json.dumps(obj_data, sort_keys=True, indent=4,
        separators=(',', ': '))

    try:
        ofd = open(file_path, "w")
        ofd.write(json_data)
        ofd.close()
    except:
        msg = "Error: cannot write data to file %s" % file_path
        log_this(msg)

    return msg

def get_connected_resource(req, board_map, resource_type):
    # look up connected resource type in board map
    resource = board_map.get(resource_type, None)
    if not resource:
        msg = "Could not find a %s resource connected to board '%s'" % (resource_type, board)
        req.send_response(RSLT_FAIL, msg)
        return None

    rmap = get_object_map(req, "resource", resource)
    return rmap

def return_api_object_data(req, obj_type, obj_name):
    # do default action for an object - return json file data (as a string)
    data = get_api_object_map(req, obj_type, obj_name)

    if not data:
        return

    # perform any data transformations required for compliance with spec
    # FIXTHIS - should manage this schema with TimeSys

    if obj_type == "board":
        # fill in dynamic board status data
        (result, msg) = get_power_status(req, data)
        data["power_status"] = msg
        (result, msg) = get_network_status(req, data)
        data["network_status"] = msg
        (result, msg) = get_command_status(req, data)
        data["command_status"] = msg

    req.send_api_response(RSLT_OK, data)

def get_interpolated_str(s, map1, map2={}):
    var_dict = map1.copy()
    var_dict.update(map2)

    # do substitution of variables from var_dict
    # do multiple passes, until no more variable references are found
    # (in case variable references are nested)
    var_ref_pattern = "%\([a-zA-Z0-9_]*\)s"
    while re.search(var_ref_pattern, s):
        s = s % var_dict

    dlog_this("interpolated string='%s'" % s)
    return s

# execute a resource command
# returns a tuple of (result, string)
def exec_command(req, board_map, resource_map, res_cmd):
    # lookup command to execute in resource_map
    res_cmd_str = res_cmd + "_cmd"
    if res_cmd_str not in resource_map:
        msg = "Resource '%s' does not have %s attribute, cannot execute" % (resource_map["name"], res_cmd_str)
        return (RSLT_FAIL, msg)

    cmd_str = resource_map[res_cmd_str]
    log_this("cmd_str=%s" % cmd_str)

    icmd_str = get_interpolated_str(cmd_str, board_map, resource_map)
    rcode, output = lc_getstatusoutput(req, icmd_str)
    dlog_this("exec_command: output=%s" % output)
    if rcode:
        msg = "Result of %s operation on resource %s = %d" % (res_cmd, resource_map["name"], rcode)
        msg += "command output='%s'" % output
        return (RSLT_FAIL, msg)

    return (RSLT_OK, output)

# execute a resource command
def return_exec_command(req, board_map, resource_map, res_cmd):
    (result, msg) = exec_command(req, board_map, resource_map, res_cmd)
    req.send_api_response_msg(result, msg)

# lookup resource either just by type, or by type and provided feature
def do_board_get_resource(req, board, board_map, rest):
    res_type = rest[0]
    del(rest[0])
    if res_type not in ["power-controller", "power-measurement", "serial",
            "canbus", "camera", "audio"]:
        msg = "Error: invalid resource type '%s'" % res_type
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    resource_to_look_up = res_type.replace("-","_")

    if not rest:
        # find resource by resource type
        resource = board_map.get(resource_to_look_up, None)
        if not resource:
            msg = "Error: no resource type '%s' associated with board '%s'" % \
                     (res_type, board)
            req.send_api_response_msg(RSLT_FAIL, msg)
            return
    else:
        # find resource by connected feature
        feature = urllib.parse.unquote(rest[0])
        resource, msg = find_resource(req, board, feature)
        if not resource:
            req.send_api_response_msg(RSLT_FAIL, msg)
            return

    req.send_api_response(RSLT_OK, { "data": resource })
    return

def do_board_camera_operation(req, board, board_map, rest):
    #check board reservation
    operation = "camera " + rest[0]
    if not user_has_board_reserved(req, board_map, operation):
        return

    cam_map = get_connected_resource(req, board_map, "camera")
    if not cam_map:
        msg = "No camera resource found for board %s" % board
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    try:
        action = rest[0]
    except IndexError:
        action = "status"

    if action == "image":
        filename = "%s-%s-image.jpeg" % (board, get_timestamp())
        filepath = req.config.files_dir + "/" + filename
        d = copy.deepcopy(cam_map)
        d["output"] = filepath
        (result, msg) = exec_command(req, board_map, d, action)
        if result == RSLT_FAIL:
            req.send_api_response_msg(result, msg)
            return

        # have message include link to image
        # FIXTHIS - this returns a relative URL path, not a full path
        file_link = req.config.files_url_base + "/files/" + filename
        msg = file_link
        # create symlink lc-data/files/{board}-last-camera-image.jpeg
        sympath = req.config.files_dir + "/%s-last-camera-image.jpeg" % board
        dlog_this("making symlink: filename=%s, sympath=%s" % (filename, sympath))

        try:
            os.unlink(sympath)
        except OSError:
            pass

        try:
            os.symlink(filename, sympath)
        except OSError:
            log_this("Problem creating symlink %s -> %s" % (sympath, filename))

        req.send_api_response_msg(result, msg)
    elif action == "video":
        filename = "%s-%s-video.mp4" % (board, get_timestamp())
        filepath = req.config.files_dir + "/" + filename
        d = copy.deepcopy(cam_map)
        d["output"] = filepath

        (result, msg) = exec_command(req, board_map, d, action)
        if result == RSLT_FAIL:
            req.send_api_response_msg(result, msg)
            return

        # have message include link to video
        file_link = req.config.files_url_base + "/files/" + filename
        msg = file_link
        # create symlink lc-data/files/{board}-last-camera-video.mp4
        sympath = req.config.files_dir + "/%s-last-camera-video.mp4" % board
        dlog_this("making symlink: filename=%s, sympath=%s" % (filename, sympath))
        try:
            os.unlink(sympath)
        except OSError:
            pass

        try:
            os.symlink(filename, sympath)
        except OSError:
            log_this("Problem creating symlink %s -> %s" % (sympath, filename))

        req.send_api_response_msg(result, msg)
    elif action == "live_stream_start":
        req.show_live_stream(cam_map)
        return_exec_command(req, board_map, cam_map, action)
        return
    elif action == "live_stream_stop":
        return_exec_command(req, board_map, cam_map, action)
        return
    else:
        msg = "camera action '%s' not supported" % action
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

def do_board_webterm_operation(req, board, board_map, rest):
    #check board reservation
    operation = "webterm " + rest[0]
    if not user_has_board_reserved(req, board_map, operation):
        return

    try:
        action = rest[0]
    except IndexError:
        msg = "Missing action for webterm request"
        log_this(msg)
        req.send_api_response_msg(RSLT_FAIL, msg)

    if action == "stop":
        msg = stop_webterm_process(req, board)
        if msg.startswith("stderr output"):
            result = RSLT_OK
            # save stderr output from ttyd for debugging
            log_this("stderr output from webterm process is:\n" + msg)
        else:
            result = RSLT_FAIL
        req.send_api_response_msg(result, msg)
        return
    else:
        msg = "webterm action '%s' not supported" % action
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

# check that the current user has the board reserved
# Returns True if user has board reserved
# Returns False if user does not have board reserved
#   Also, a message is sent to the user indicating the failure.
def user_has_board_reserved(req, board_map, operation, return_api_response=True):
    assigned_to = board_map.get("AssignedTo", "nobody")
    if req.get_user() == assigned_to:
        return True

    msg = """Device is not assigned to you. It is assigned to '%s'.
Cannot do %s operation.""" % (assigned_to, operation)
    if return_api_response:
        req.send_api_response_msg(RSLT_FAIL, msg)
    return False

def do_status_operation(req, board, board_map, rest):
    if len(rest):
        item = rest[0]
        if item not in ["power", "network", "command"]:
            msg = "%s operation is not supported. Please check help" % rest[0]
            req.send_api_response_msg(RSLT_FAIL, msg)
            return
    else:
        item = ""

    if item:
        if item == "power":
            (result, msg) = get_power_status(req, board_map)
        elif item == "network":
            (result, msg) = get_network_status(req, board_map)
        elif item == "command":
            (result, msg) = get_command_status(req, board_map)
        req.send_api_response_msg(result,msg)
    else:
        msg = "bare status operation is not supported"
        req.send_api_response_msg(RSLT_FAIL, msg)
        return
    return


def do_board_power_operation(req, board, board_map, rest):
    operation = "power " + rest[0]
    if not user_has_board_reserved(req, board_map, operation):
        return

    pdu_map = get_connected_resource(req, board_map, "power_controller")
    if not pdu_map:
        msg = "No power controller resource found for board %s" % board
        req.send_api_response_msg(RSLT_FAIL,  msg)
        return

    try:
        action = rest[0]
    except IndexError:
        action = "status"

    if action == "status":
        (result, msg) = get_power_status(req, board_map)
        log_this("power status result=%s,%s" % (result, msg))
        if result==RSLT_OK:
            req.send_api_response(result, {"data": msg})
            return
        else:
            req.send_api_response_msg(result, msg)
            return
    elif action in ["on", "off", "reboot"]:
        return_exec_command(req, board_map, pdu_map, action)
        return
    else:
        msg = "power action '%s' not supported" % action
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

# if a reservation has expired, clear it now
# if reservation expired, return True
def check_reservation_expiry(req, board_map):
    end_time_str = board_map.get("end_time", "unknown")

    dlog_this("in check_reservation_expiry for board %s, end_time=%s" % (board_map["name"], end_time_str))

    if end_time_str in ["never", "unknown", "0-0-0_0:0:0"]:
        return False

    try:
        end_time = datetime.datetime.strptime(end_time_str, "%Y-%m-%d_%H:%M:%S")
    except ValueError:
        log_this("in check_reservation_expiry: Invalid end time '%s' for board %s" % (end_time_str, board_map["name"]))
        return False

    now = datetime.datetime.now()
    if now > end_time:
        log_this("Expiring (at %s) reservation by %s on board %s, which ended %s" % (now, board_map["AssignedTo"], board_map["name"], end_time_str))
        msg = clear_reservation(req, board_map)
        if msg:
            log_this("in check_reservation_expiry: Error: %s trying to clear reservation" % msg)
            return False
        return True

    return False


# returns a message in case of error
def clear_reservation(req, board_map):
    board_map["AssignedTo"] = "nobody"
    board_map["start_time"] = "0-0-0_0:0:0"
    board_map["end_time"] = "0-0-0_0:0:0"

    board = board_map["name"]

    # save data back to json file
    msg = save_object_data(req, "board", board, board_map)

    # terminate web terminal process, if any (ignore any errors)
    stop_webterm_process(req, board)
    return msg

def do_board_assign(req, board, board_map, rest):
    if check_reservation_expiry(req, board_map):
        # reload the board_map; the reservation changed
        board_map = get_object_map(req, "board", board)

    duration = ""
    if rest:
        try:
            duration = int(rest[0])
        except ValueError:
            msg = "Invalid duration '%s' in assign operation" % rest[0]
            req.send_api_response_msg(RSLT_FAIL, msg)
        del(rest[0])
        if rest:
            msg = "extra data '%s' in assign operation" % str(rest)
            req.send_api_response_msg(RSLT_FAIL, msg)
            return

    if not duration:
        # get default reservation duration from server config
        duration = req.config.default_reservation_duration

    # get current user, and add reservation for board to user
    user = req.get_user()
    assigned_to = board_map.get("AssignedTo", "nobody")
    if assigned_to != "nobody":
        end_time = board_map.get("end_time", "unknown")
        if user == assigned_to:
            msg = "Device is already assigned to you, ending %s" % end_time
        else:
            msg = "Device is already assigned to %s, ending %s" % (assigned_to, end_time)
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    if user and user != "nobody":
        board_map["AssignedTo"] = user

        start_time = datetime.datetime.now()
        log_this("Start time of reservation=%s" % start_time)
        board_map["start_time"] = start_time.strftime("%Y-%m-%d_%H:%M:%S")

        if duration != "forever":
            # duration might still be a string, if read from config
            y = datetime.timedelta(minutes=int(duration))
            end_time = start_time + y
            board_map["end_time"] = end_time.strftime("%Y-%m-%d_%H:%M:%S")
        else:
            board_map["end_time"] = "never"
    else:
        msg = "Cannot determine user for operation"
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    # save data back to json file
    msg = save_object_data(req, "board", board, board_map)

    if msg:
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    end_time = board_map["end_time"]
    if end_time == "never":
        end_time = "forever"
    msg = "Board %s is assigned to you from %s until %s" % \
            (board, board_map["start_time"], end_time)
    req.send_api_response_msg(RSLT_OK, msg)
    return

def do_board_release(req, board, board_map, rest):
    # get current user, and remove reservation for board
    user = req.get_user()

    if check_reservation_expiry(req, board_map):
        # reload the board_map; the reservation changed
        board_map = get_object_map(req, "board", board)

    assigned_to = board_map.get("AssignedTo", "nobody")

    if assigned_to == "nobody":
        msg = "Device is already free and available for allocation."
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    if not user or user == "nobody":
        msg = "Cannot determine user for operation"
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    force = False
    if rest and rest[0] == "force":
        force = True

    if not force:
        if user != assigned_to:
            msg = "Device is not assigned to you. It is assigned to '%s'.\nCannot release it. (try using 'force' option)" % assigned_to
            req.send_api_response_msg(RSLT_FAIL, msg)
            return

    msg = clear_reservation(req, board_map)

    if msg:
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    msg = "Board %s is no longer reserved." % board
    req.send_api_response_msg(RSLT_OK, msg)
    return

def do_board_run(req, board, board_map, rest):
    # check board permission
    if not user_has_board_reserved(req, board_map, "run"):
        return

    # get the command to run
    try:
        run_data = req.form.value.decode("utf-8")
        dlog_this("run_data=%s" % run_data)
        command_to_run = json.loads(run_data).get("command", "")
    except (TypeError, AttributeError):
        command_to_run = req.form.getfirst("command", "")

    dlog_this("command_to_run=%s" % command_to_run)
    if not command_to_run:
        msg = "Cannot parse 'command' from form data (or it was empty)"
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    cmd_str = board_map.get("run_cmd", None)

    if not cmd_str:
        msg = "Device '%s' is not configured to run commands" % board
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    run_map = { "command": command_to_run }
    cmd_str = get_interpolated_str(cmd_str, board_map, run_map)

    log_this("About to run_command '%s' on board %s" % (cmd_str, board_map["name"]))

    rcode, output, msg = run_command(req, cmd_str)
    if msg:
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    # keep the newlines on the lines
    lines = output.splitlines(True)
    dlog_this("output lines=%s" % lines)

    data = { "return_code": rcode, "data": lines }
    jdata = json.dumps(data)
    dlog_this("jdata='%s'" % jdata)

    req.send_api_response(RSLT_OK, { "data": data } )
    return

# this is experimental code for a better 'run' operation
# stream the data back the client, interleaving stdout and stderr
# also encode the final rcode in the stream
# markers are:
#
# marker_err = b"&now^for#std%err"
# marker_out = b"&now^for#std%out"
# marker_rcode = b"&now^for#rcode%"
# rcode marker must be followed by an exactly 3-character string representing
# the return code of the executed process (0-255)

def do_board_run2(req, board, board_map, rest):
    # check that user has board reserved
    user = req.get_user()
    assigned_to = board_map.get("AssignedTo", "nobody")

    # FIXTHIS - need to add authentication token to run2 request from lc
    dlog_this("TRB: user=%s" % user)

    # FIXTHIS - remove this check for now
    #if user != assigned_to:
    #    msg = "Device is not assigned to you. It is assigned to '%s'.\nCannot run command." % assigned_to
    #    req.send_api_response_msg(RSLT_FAIL, msg)
    #    return

    # This seems optimistic - maybe add some error handling here
    run_data = req.form.value.decode("utf-8")
    dlog_this("run_data=%s" % run_data)
    try:
        command_to_run = json.loads(run_data).get("command", "")
    except TypeError:
        command_to_run = req.form.getfirst("command", "")

    dlog_this("command_to_run=%s" % command_to_run)
    if not command_to_run:
        msg = "Cannot parse 'command' from form data (or it was empty)"
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    cmd_str = board_map.get("run_cmd", None)

    if not cmd_str:
        msg = "Device '%s' is not configured to run commands" % board
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    run_map = { "command": command_to_run }
    cmd_str = get_interpolated_str(cmd_str, board_map, run_map)

    log_this("About to run_command '%s' on board %s" % (cmd_str, board_map["name"]))

    # HERE is where things diverge from do_board_run()
    #rcode, output, msg = run_command(req, cmd_str)
    from subprocess import Popen, PIPE, STDOUT

    exec_args = shlex.split(cmd_str)

    marker_err = b"&now^for#std%err"
    marker_out = b"&now^for#std%out"
    marker_rcode = b"&now^for#rcode%"

    output = ""

    # FIXTHIS - add more stuff here

    try:
        proc = Popen(exec_args, stdin=PIPE, stdout=PIPE, stderr=PIPE, close_fds=True, universal_newlines=True, bufsize=1)
    except subprocess.CalledProcessError as e:
        msg = "Can't run command '%s' in exec_command" % cmd
        req.send_api_response_msg(RSLT_FAIL, msg)
    except OSError as error:
        msg = error + " trying to execute command '%s'" % cmd
        req.send_api_response_msg(RSLT_FAIL, msg)

    # don't allow command to run for more than 24 hours
    timer = threading.Timer(60.0*60.0*24, run_timeout, [proc])
    timer.start()

    pid = proc.pid

    # Binary data needs to be sent directly
    sys.stdout.write("Content-type: text/plain; charset=utf-8\n\n")

    # FIXTHIS - read stdout and stderr from proc, and send as we get it
    try:
        output, errs = proc.communicate()
    except:
        proc.kill()
        output, errs = proc.communicate()

    timer.cancel()

    # should check req.is_cgi here.  this will need to be different for wsgi
    # FIXTHIS - send back test data
    sys.stdout.write("Here is some test data\n")
    sys.stdout.write("this should be stdout\n")
    sys.stdout.write(marker_err + "   EEEE - this should be stderr\n")
    sys.stdout.write(marker_out + "more stdout data\n")
    sys.stdout.write(marker_err + "   EEEE - more stderr data\n")
    sys.stdout.write(marker_out + "last line of data\n")
    sys.stdout.flush()

    rcode = proc.returncode

    # send the data back here
    rcode_str = "%03d" % rcode
    log_this("rcode_str='%s'" % rcode_str)
    sys.stdout.write(marker_rcode+rcode_str)
    sys.stdout.flush()
    sys.exit(0)

# Notes parses the binary data into a dictionary
# all parts of the data are decoded using 'utf-8' into python str types,
# with the file data, which is left as binary
def parse_multipart(data):
    boundary = data[:256].split(b"\r\n")[0]
    #dlog_this("boundary='%s'" % boundary)
    if not boundary.startswith(b"--"):
        return {}

    data_dict = {}
    section_list = data.split(boundary)
    #dlog_this("section_list=%s" % section_list)
    for section in section_list:
        #dlog_this("section='%s'" % section)
        if not section or section == b"--\r\n":
            continue
        if not section.startswith(b"\r\nContent-Disposition: form-data;"):
            log_this("unrecognized section in form data: '%s'" % section.decode("utf-8"))
            continue
        key = section.split(b"name=")[1].split(b";")[0].split(b'\r')[0].replace(b'"',b'').decode("utf-8")
        #dlog_this("key=%s" % key)
        try:
            val_parts = section.split(b"\r\n")[3:-1]
        except IndexError:
            log_this("unrecognized syntax in form data: '%s'" % section.decode("utf-8"))
            continue
        #dlog_this("val_parts=%s" % val_parts)
        value = b"\r\n".join(val_parts)
        #dlog_this("value='%s'" % value)
        if key=="file":
            data_dict[key] = value
        else:
            data_dict[key] = value.decode("utf-8")

        if key=="file":
            filename = section.split(b"filename=")[1].split(b";")[0].split(b'\r')[0].replace(b'"',b'').decode("utf-8")
            data_dict["filename"] = filename

    return data_dict

# upload operation:
#   get data from request and put it into a local file
#   use "upload_cmd" command to transfer it to the board
#     (set src, dest in the command)
# files (and directories) are staged on the host.
# directories are sent as tarballs, and are staged and extracted
# on the host.
# This code assumes that the upload_cmd (in the board JSON file)
# can handle individual files as well as recursive directory copies
# from the host to the target.
def do_board_upload(req, board, bmap, rest):
    # check that user has board reserved
    if not user_has_board_reserved(req, bmap, "upload"):
        return

    # get data for the upload
    bin_form_data = req.form.value
    form_is_from_client = True
    if b"Content-Disposition:" in bin_form_data:
        form_dict = parse_multipart(bin_form_data)
    else:
        form_is_from_client = False
        form_dict = req.form

    if form_is_from_client:
        dest_path = form_dict["path"]
        filename = os.path.basename(form_dict["filename"])
        data = form_dict["file"]
        extract = form_dict.get("extract", "false")
        perms = form_dict.get("permissions", None)
    else:
        dest_path = form_dict["path"].value
        file_item = form_dict["file"]
        # this prints out the full file contents, uncomment for more debug data
        #dlog_this("file_item='%s'" % (str(file_item)))
        filename = file_item.filename
        data = file_item.file.read()
        extract = "false"
        perms = None

    dlog_this("dest_path=%s" % dest_path)
    dlog_this("filename=%s" % filename)
    # this is usually too long to show, but uncomment this for extra debug data
    #dlog_this("data='%s'" % data)
    dlog_this("extract=%s" % extract)
    dlog_this("perms=%s" % perms)

    # make sure board supports upload operation
    cmd_str = bmap.get("upload_cmd", None)
    if not cmd_str:
        msg = "Device '%s' is not configured with upload_cmd" % board
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    # read file from form data into temp file on lcserver host system
    tmpdir = tempfile.mkdtemp()
    staged_path=tmpdir + "/" + filename
    with open(staged_path, "wb") as fd:
        fd.write(data)

    if extract != "true" and perms:
        # set permissions on the staged file
        # note: perms is an octal string (without a leading 0)
        os.chmod(staged_path, int(perms, 8))

    if extract == "true":
        # extract the tarball into the stage directory
        tar_cmd = "tar -C %s -xf %s" % (tmpdir, staged_path)

        rcode, output = getstatusoutput(tar_cmd)
        if rcode:
            msg = "Could not extract data for directory upload\n"
            msg += "tar output=%s" % output
            req.send_api_response_msg(RSLT_FAIL, msg)
            return

        # remove .tar.gz from filename to get directory name
        staged_path = staged_path[:-7]
        filename = filename[:-7]

    # do a file or directory upload
    upload_map = { "src": staged_path, "dest": dest_path }
    icmd_str = get_interpolated_str(cmd_str, bmap, upload_map)

    log_this("Executing upload command: %s" % icmd_str)
    rcode, output = lc_getstatusoutput(req, icmd_str)

    # clean up temporary files and directories
    import shutil
    shutil.rmtree(tmpdir)

    if rcode:
        msg = "Could not perform upload operation on board %s\n" % board
        msg += "command output='%s'" % output
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    msg = "%s uploaded" % filename
    req.send_api_response_msg(RSLT_OK, msg)
    return

# download operation:
#   get data from request
#   use "download_cmd" command to get file or directory from the board
#     (set src, dest in the command, with dest being a host staging area)
#   and send file as a tarball
# files (and directories) are staged on the host.
# both files and directories are tarballs, and are staged and extracted
# on the client.
# This code assumes that the download_cmd (in the board JSON file)
# can handle individual files as well as recursive directory copies
# from the target board to the host.
def do_board_download(req, board, bmap, rest):
    # check that user has board reserved
    if not user_has_board_reserved(req, bmap, "download"):
        return

    # get data for the download

    compress = req.form.getfirst("compress", "false")
    src_path = req.form.getfirst("path", None)
    if not src_path:
        msg = "Download request is missing path\nCannot do download."
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    dlog_this("src_path=%s" % src_path)

    # make sure board supports download operation
    cmd_str = bmap.get("download_cmd", None)
    if not cmd_str:
        msg = "Device '%s' is not configured with download_cmd" % board
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    # create staging directory for file transfer
    tmpdir = tempfile.mkdtemp()
    src_parent_dir = os.path.dirname(src_path)
    staged_path = tmpdir + src_parent_dir
    os.makedirs(staged_path)

    download_map = { "src": src_path, "dest": staged_path }
    icmd_str = get_interpolated_str(cmd_str, bmap, download_map)

    log_this("Executing download command: %s" % icmd_str)
    rcode, output = lc_getstatusoutput(req, icmd_str)
    if rcode:
        msg = "Could not perform download operation on board %s\n" % board
        msg += "command output=%s" % output
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    # convert the staged data to a tarball
    tar_path = tmpdir + "/" + os.path.basename(src_path) + ".tar.gz"
    tar_cmd = "tar -C %s -czf %s %s" % (tmpdir, tar_path, src_path[1:])

    dlog_this("Executing tar_cmd: %s" % tar_cmd)
    rcode, output = getstatusoutput(tar_cmd)
    if rcode:
        msg = "Could not create tarfile for download\n"
        msg += "tar output=%s" % output
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    # read file from temp area and write to output
    with open(tar_path, "rb") as fd:
        bin_data = fd.read()

    # write file as response
    # Content-type: text/html; charset=utf-8
    # Content-Disposition: attachment; filename="%s" % path
    # Vary: Accept,Cookie,Accept-Encoding
    # Allow: GET, HEAD, OPTIONS
    # Content-length: xxx
    # {data}

    # Binary data needs to be sent directly:

    # this was using the html.append method of returning the response
    #req.html.append("Content-type: text/plain; charset=utf-8\n")
    ##req.html.append('Content-Disposition: attachment; filename="%s"\n' % src_path)
    ##req.html.append("Content-Length: %d\n" % len(data))
    #req.html.append(data)

    # output the data directly
    # should check req.is_cgi here.  this will need to be different for wsgi
    sys.stdout.buffer.write(b"Content-type: text/plain; charset=utf-8\n\n")
    # write data as binary data # (sys.stdout.write() sends a string)
    sys.stdout.buffer.write(bin_data)
    sys.stdout.flush()

    # clean up staged files and directories
    import shutil
    shutil.rmtree(tmpdir)

    sys.exit(0)


# rest is a list of the rest of the path
# supported actions are: get_resource, power, assign, release, run
def return_api_board_action(req, board, action, rest):
    dlog_this("rest=%s" % rest)
    boards = get_object_list(req, "board")
    if board not in boards:
        msg = "Could not find board '%s' registered with server" % board
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    board_map = get_object_map(req, "board", board)
    if not board_map:
        msg = "Problem loading data for board '%s'" % board
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    if action == "get_resource":
        do_board_get_resource(req, board, board_map, rest)
        return

    elif action == "power":
        do_board_power_operation(req, board, board_map, rest)
        return

    elif action == "assign":
        duration = req.form.getfirst("duration", "")
        log_this("duration=%s minutes" % duration)
        if duration:
            rest = list(duration.strip().split(" "))
        do_board_assign(req, board, board_map, rest)
        return

    elif action == "release":
        do_board_release(req, board, board_map, rest)
        return

    elif action == "run":
        do_board_run(req, board, board_map, rest)
        return

    elif action == "run2":
        do_board_run2(req, board, board_map, rest)
        return

    elif action == "upload":
        do_board_upload(req, board, board_map, rest)
        return

    elif action == "download":
        do_board_download(req, board, board_map, rest)
        return

    elif action == "camera":
        do_board_camera_operation(req, board, board_map, rest)
        return

    elif action == "status":
        do_status_operation(req, board, board_map, rest)
        return

    elif action == "webterm":
        do_board_webterm_operation(req, board, board_map, rest)
        return

    msg = "action '%s' not supported (rest='%s')" % (action, rest)
    req.send_api_response_msg(RSLT_FAIL, msg)


# these format strings should be
# be interpolated with (resource, token, extension)
CAPTURE_FILENAME_FMT="%s-capture-%s%s"
VIDEO_FILENAME_FMT="%s-video-%s%s"
CAPTURE_PID_FILENAME_FMT="/tmp/capture-%s-%s.pid"

data_dir="/tmp"
data_prefix="data-file-"
data_suffix=".data"

# returns a tuple with (pid, msg)
# On success, pid is non-zero and msg is empty.
# On failure, pid is 0 and msg is non-empty.
# It puts stdout and stderr into files, using the token as part of the filename
#
# This only executes a single-line command, for now
def start_command(req, token, cmd):
    from subprocess import Popen, PIPE, STDOUT

    exec_args = shlex.split(cmd)

    # FIXTHIS - handle multi-line commands in start_command()

    capture_stdout = open("/tmp/capture-stdout-" + token, "wb")
    capture_stderr = open("/tmp/capture-stderr-" + token, "wb")

    # if program filename is not a path, look for it in the 'utils' dir
    program_name = exec_args[0]
    if "/" not in program_name:
        utils_dir = os.path.abspath(req.config.base_dir + "/../utils/")
        prog_path = utils_dir + "/" + program_name
        if os.path.isfile(prog_path):
            # substitute the utils program path for the original program name
            exec_args[0] = prog_path

    try:
        proc = Popen(exec_args, stdin=PIPE, stdout=capture_stdout, stderr=capture_stderr, close_fds=True, start_new_session=True)
    except subprocess.CalledProcessError as e:
        msg = "Can't run command '%s' in exec_command" % cmd
        return (0, msg)
    except OSError as error:
        msg = str(error) + " trying to execute command '%s'" % cmd
        return (0, msg)

    pid = proc.pid
    return (pid, "")

def run_timeout(proc):
    log_this("run_timeout fired! - killing process %s" % proc.pid)
    proc.kill()

# special (lc version of) getstatusouput
# return rcode, output from getstatusoutput from command
# the difference with this command is that it supports running
# items from the labcontrol utils directory
def lc_getstatusoutput(req, cmd):
    try:
        program_name=shlex.split(cmd)[0]
    except ValueError:
        msg = req.html_error("Error: Could not parse program name for cmd '%s'\n" % cmd)
        return (1, msg)

    # if program filename is not a path, look for it in the 'utils' dir
    if "/" not in program_name:
        utils_dir = os.path.abspath(req.config.base_dir + "/../utils/")
        prog_path = utils_dir + "/" + program_name
        if os.path.isfile(prog_path):
            # substitute the utils program path for the original program name in
            # the command string (slice off the original program name)
            args = cmd[len(program_name):]
            cmd = prog_path + " " + args

    dlog_this("cmd in lc_getstatusoutput is: %s" % cmd)
    return getstatusoutput(cmd)

# run_command - run a single line command
# returns: return_code, output, reason
# where return_code is the exit code
# of the command, and lines is an array of the command output.
#
# On failure, reason is non-empty and contains a description of the problem.
#
def run_command(req, cmd):
    from subprocess import Popen, PIPE, STDOUT

    exec_args = shlex.split(cmd)

    output = ""

    # FIXTHIS - add more stuff here

    try:
        proc = Popen(exec_args, stdin=PIPE, stdout=PIPE, stderr=PIPE, close_fds=True)
    except subprocess.CalledProcessError as e:
        msg = "Can't run command '%s' in exec_command" % cmd
        return (0, output, msg)
    except OSError as error:
        msg = error + " trying to execute command '%s'" % cmd
        return (0, output, msg)

    # don't allow command to run for more than 300 seconds (5 minutes)
    timer = threading.Timer(300.0, run_timeout, [proc])
    timer.start()

    # we may want to have a different interface here, that allows
    # starting the command, and returning partial data after 10 seconds
    # followed by periodic queries from the client to get additional data
    # before finally yeilding the final data and return code
    # we would need a file buffer and pid system, like for the capture code.

    pid = proc.pid
    try:
        output, errs = proc.communicate()
    except:
        proc.kill()
        output, errs = proc.communicate()

    timer.cancel()

    # FIXTHIS - run_command discards error output

    rcode = proc.returncode
    log_this("rcode=%s" % rcode)

    log_this("output='%s'" % output)
    return (rcode, output, None)

# returns non-empty reason string on failure
def set_config(req, action, resource_map, config_map, rest):
    msg = ""

    if not config_map:
        return "No configuration items were provided to set-config operation"


    resource = resource_map["name"]
    config_cmd = resource_map.get("config_cmd", "")
    if not config_cmd:
        return "Could not find 'config_cmd' for resource resource %s" %  resource

    dlog_this("config_cmd=" + config_cmd)

    allowed_config_items=["baud_rate"]

    # FIXTHIS - should also add variables from the board_map here
    # this might require a separate lookup to see what board we're operating on
    new_resource_map = resource_map.copy()

    # only copy allowed items from config_map
    for key, value in list(config_map.items()):
        if key in allowed_config_items:
            new_resource_map[key] = value

    icmd_str = get_interpolated_str(config_cmd, new_resource_map)
    rcode, output = lc_getstatusoutput(req, icmd_str)
    if rcode:
        msg = "Result of set-config operation on resource %s = %d\n" % (resource, rcode)

        # Apparently, 'stty' error output uses some high unicode code points,
        # (outside the ascii range), which cause an exception if you don't
        # decode them. (ie, they get auto-decoded as ascii)
        # Have I told you, lately, how much I hate python unicode handling??
        output = output.decode('utf8', errors='ignore')
        msg += "command output (decoded)='" + output + "'"
        return msg

    # write out updated resource_map
    msg = save_object_data(req, "resource", resource, new_resource_map)

    return None

# returns a filename appropriate for this type of captured data
def get_capture_filepath(req, res_type, res_map, token):
    # could put files into different directories, based on resource or type
    # but defer this feature for later
    cdir = req.config.files_dir + "/"


    ext = ".txt"
    if res_type == "camera":
        try:
            ext = resource_map["video_filename_extension"]
        except:
            log_this("Missing video_filename_extension in definition of resource '%s'" % res_map["name"])
            ext = ".mp4"

        return cdir + VIDEO_FILENAME_FMT % (res_map["name"], token, ext)

    if res_type == "audio":
        ext = ".au"

    return cdir + CAPTURE_FILENAME_FMT % (res_map["name"], token, ext)


# returns token, reason
# on error, token is None or empty and reason is a string with an error
# message.  The error message should start with "Error: "
def start_capture(req, res_type, resource_map, rest):
    # look up capture_cmd in resource_map, and call it
    resource = resource_map["name"]
    capture_cmd = resource_map.get("capture_cmd")

    log_this("capture_cmd=" + capture_cmd)

    token = get_timestamp()

    pidfile = CAPTURE_PID_FILENAME_FMT % (resource, token)

    capture_file = get_capture_filepath(req, res_type, resource_map, token)

    if os.path.exists(pidfile):
        # FIXTHIS - in start_capture(), check if process is still running
        # if not, just remove pidfile, and continue
        return ("", "Capture is already running for resource %s" % resource)

    # do string interpolation from the data in the resource map
    # (adding the 'logfile' attribute)
    d = copy.deepcopy(resource_map)
    d["logfile"] = capture_file
    d["output"] = capture_file

    if res_type == "camera":
        if rest:
            d["duration"] = rest[0]
        else:
            d["duration"] = req.config.default_video_recording_duration

    try:
        cmd = capture_cmd % d
    except KeyError as e:
        return ("", "Problem interpolating capture_cmd in resource definition of '%s', msg=KeyError: %s" % (resource, str(e)))

    dlog_this("(interpolated) cmd=" + cmd)

    # save pid and capture filename in a file, named with the token
    pid, msg = start_command(req, token, cmd)
    if not pid:
        log_this("exec failure: reason=" + msg)
        return ("", msg)

    log_this("capture pid=%d" % pid)
    fd = open(pidfile,"w")
    fd.write(str(pid) + "\n" + capture_file)
    fd.close()

    return (token, "")

# capture - capture a still image
# returns url_path, reason
# on error, url_path is None or empty and reason is a string with an error
# message.  The error message should start with "Error: "
# on success, url_path has the URL where the data can be found on the server
def capture(req, res_type, resource_map, rest):
    # generate a filename for the still image to be captured
    resource = resource_map["name"]
    if res_type == "camera":
        data_type = "image"
    else:
        data_type = "data"
    filename = "%s-%s-%s.jpeg" % (resource, get_timestamp(), data_type)
    filepath = req.config.files_dir + "/" + filename
    d = copy.deepcopy(resource_map)
    d["output"] = filepath

    # FIXTHIS - does 'capture' operation need race protection here?
    (result, msg) = exec_command(req, {}, d, data_type + "_capture")
    if result == RSLT_FAIL:
        return ("", msg)

    url_path = req.config.url_prefix + req.config.files_url_base + "/files/" + filename

    return (url_path, "")

# returns msg with:
#   msg = empty on success, non-empty on failure
# msg has the stderr output, if any, of the command on failure
def stop_capture(req, res_type, resource_map, token, rest):
    resource = resource_map["name"]

    pidfile = CAPTURE_PID_FILENAME_FMT % (resource, token)

    if not os.path.exists(pidfile):
        return "Cannot find pidfile to stop capture for token '%s'" % token

    # Could support optional stop_cmd execution here
    # but let's wait on that.

    # NOTE: if we're running on a Linux kernel with a version later than
    # 5.4, we could use pidfd_open and waitid to get the return code for
    # the process, and check that for an error indication.

    # for now, just return stderr as msg, if it's non-empty

    try:
        fd = open(pidfile, "r")
        pid = int(fd.read().split('\n')[0].strip())
        fd.close()
    except IOError:
        pid = None

    if not pid:
        return "Cannot find in-progress capture for %s for resource '%s'" % (action, resource)

    sigterm_retry_wait = [0.5, 0.5, 1, 1, 1.5, 2, 2, 2]

    try:
        count = 0
        while count < len(sigterm_retry_wait):
            dlog_this("Sending SIGTERM to pid %d" % pid)
            os.kill(pid, signal.SIGTERM)
            time.sleep(sigterm_retry_wait[count])
            count += 1
        log_this("Exceeded max sigterm retries")
        log_this("Sending SIGKILL to pid %d" % pid)
        os.kill(pid, signal.SIGKILL)
        sleep(5)
        os.kill(pid, signal.SIGKILL)
        log_this("SIGKILL failed - process %d is still running!!" % pid)
    except OSError as err:
        err = str(err)
        if err.find("No such process") > 0:
            if os.path.exists(pidfile):
                dlog_this("Removing pidfile %s" % pidfile)
                os.remove(pidfile)

    # check for program error (stderr is non-empty)
    cout_file = "/tmp/capture-stdout-" + token
    if os.path.exists(cout_file):
        dlog_this("Removing capture stdout file %s" % cout_file)
        os.remove(cout_file)

    cerr_file = "/tmp/capture-stderr-" + token
    msg = ""
    if os.path.exists(cerr_file):
        stderr = open(cerr_file, "r").read()
        if stderr:
            msg = "stderr output from capture command: '%s'" % stderr
            log_this(msg)
        dlog_this("Removing capture stderr file %s" % cerr_file)
        os.remove(cerr_file)

    return msg

# returns data, reason
# data is empty on failure, and reason is a string with error message
# otherwise, sends data from capture.  Captured data may be transformed
# from its original format, but in all cases should be sent as json.
def get_captured_data(req, res_type, resource_map, token, rest):
    resource = resource_map["name"]

    capture_file = get_capture_filepath(req, res_type, resource_map, token)

    if not os.path.exists(capture_file):
        return (None, "Cannot find capture file for resource '%s', token %s" % (resource, token))

    try:
        fd = open(capture_file, "r")
        capture_data = fd.read()
        fd.close()
    except IOError:
        log_data = None

    if not capture_data:
        return (None, "Cannot read capture data for resource '%s', token %s" % (resource, token))

    # convert to json data
    # FIXTHIS - should not use hardcoded re-format operation here, for sdb data
    # should run a conversion command specified by the resource object
    if res_type == "power-measurement-output-conversion-disabled":
        jdata = "[\n"
        for line in capture_data.split("\n"):
            if not line:
                continue
            parts = line.split(",")
            try:
                jdata += ' { "timestamp": "%s", "voltage": "%s", "current": "%s" }\n' % (parts[0], float(parts[1])/1000.0, float(parts[2])/1000.0)
            except:
                log_this("Problem converting log_data line for power measurement\nline='%s'" % line)
        jdata += "]"
        return (jdata, "")

    return (capture_data, "")

# returns url_path, reason
# url_path is empty on failure, and reason is a string with error message
# otherwise, url_path has a location where the data can be downloaded
# from the server.
def get_captured_data_ref(req, res_type, resource_map, token, rest):
    resource = resource_map["name"]

    # see if capture is still running
    pidfile = CAPTURE_PID_FILENAME_FMT % (resource, token)
    if os.path.exists(pidfile):
        try:
            fd = open(pidfile, "r")
            pid = int(fd.read().split('\n')[0].strip())
            fd.close()
        except IOError:
            pid = None
        import psutil
        if pid and psutil.pid_exists(pid):
            return ("", "Capture is still running for resource %s" % resource)

    capture_file = get_capture_filepath(req, res_type, resource_map, token)

    if not os.path.exists(capture_file):
        return (None, "Cannot find capture file for resource '%s', token %s" % (resource, token))

    filename = os.path.basename(capture_file)
    url_path = req.config.url_prefix + req.config.files_url_base + "/files/" + filename
    return (url_path, "")

# returns reason on failure, "" on success
def delete_capture(req, res_type, resource_map, token, rest):
    resource = resource_map["name"]

    capture_file = get_capture_filepath(req, res_type, resource_map, token)

    if not os.path.exists(capture_file):
        return "Cannot delete captured data for resource '%s'" % resource
    os.remove(capture_file)
    return ""

def put_data(req, res_type, resource_map, rest):
    resource = resource_map["name"]
    put_cmd = resource_map.get("put_cmd", "")
    if not put_cmd:
        return "Could not find 'put_cmd' for resource resource %s" %  resource
    dlog_this("put_cmd=" + put_cmd)

    # put data into a file
    bin_data = req.form.value
    log_this("bin_data=%s" % bin_data.decode("utf-8"))

    fd, datapath = tempfile.mkstemp(data_suffix, data_prefix, data_dir)
    os.write(fd, bin_data)
    os.close(fd)

    d = copy.deepcopy(resource_map)
    d["datafile"] = datapath

    icmd_str = put_cmd % d
    dlog_this("(interpolated) cmd_str='%s'" + icmd_str)
    rcode, output = lc_getstatusoutput(req, icmd_str)
    os.remove(datapath)
    if rcode:
        msg = "Result of put operation on resource %s = %d\n" % (resource, rcode)
        output = output.decode('utf8', errors='ignore')
        msg += "command output (decoded)='" + output + "'"
        return msg

    return None


# rest is a list of the rest of the path
# supported res_types are: power-measurement, serial, camera, audio
def return_api_resource_action(req, resource, res_type, rest):
    resources = get_object_list(req, "resource")
    if resource not in resources:
        msg = "Could not find resource '%s' registered with server" % resource
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    resource_map = get_object_map(req, "resource", resource)
    if not resource_map:
        msg = "Problem loading data for resource '%s'" % resource
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    operation = rest[0]
    del(rest[0])

    if res_type in ["serial", "camera", "audio"] and operation == "set-config":
        # convert set-config form data to a simple map
        config_map = {}
        for key in list(req.form.keys()):
            config_map[key] = req.form.getvalue(key)

        dlog_this("config_map=%s" % config_map)

        msg = set_config(req, res_type, resource_map, config_map, rest)
        if msg:
            req.send_api_response_msg(RSLT_FAIL, msg)
        else:
            req.send_api_response(RSLT_OK)
        return

    if res_type in ["power-measurement", "serial", "camera", "audio"]:
        if operation in ["stop-capture", "get-data", "get-ref", "delete"]:
            try:
                token = rest[0]
            except IndexError:
                msg = "Missing token for %s operation" % res_type
                req.send_api_response_msg(RSLT_FAIL, msg)
                return

        if operation == "start-capture":
            dlog_this("rest=%s" % rest)
            token, reason = start_capture(req, res_type, resource_map, rest)
            if not token:
                req.send_api_response_msg(RSLT_FAIL, reason)
                return
            req.send_api_response(RSLT_OK, { "data": token } )
            return
        elif operation == "capture":
            data, reason = capture(req, res_type, resource_map, rest[1:])
            if not data:
                req.send_api_response_msg(RSLT_FAIL, reason)
                return
            req.send_api_response(RSLT_OK, { "data": data } )
            return
        elif operation == "stop-capture":
            reason = stop_capture(req, res_type, resource_map, token, rest[2:])
            if reason:
                req.send_api_response_msg(RSLT_FAIL, reason)
                return
            req.send_api_response(RSLT_OK)
            return
        elif operation == "get-data":
            data, reason = get_captured_data(req, res_type, resource_map, token, rest[2:])
            if reason:
                req.send_api_response_msg(RSLT_FAIL, reason)
                return
            req.send_api_response(RSLT_OK, { "data": data } )
            return
        elif operation == "get-ref":
            data, reason = get_captured_data_ref(req, res_type, resource_map, token, rest[2:])
            if reason:
                req.send_api_response_msg(RSLT_FAIL, reason)
                return
            req.send_api_response(RSLT_OK, { "data": data } )
            return
        elif operation == "delete":
            reason = delete_capture(req, res_type, resource_map, token, rest[2:])
            if reason:
                req.send_api_response_msg(RSLT_FAIL, reason)
                return
            req.send_api_response(RSLT_OK)
            return
        elif operation == "put-data":
            reason = put_data(req, res_type, resource_map, rest)
            if reason:
                req.send_api_response_msg(RSLT_FAIL, reason)
                return
            req.send_api_response(RSLT_OK)
            return
        else:
            msg = "operation '%s' not supported for %s resource" % (operation, res_type)
            req.send_api_response_msg(RSLT_FAIL, msg)
            return


    msg = "resource type '%s' not supported (rest='%s')" % (res_type, rest)
    req.send_api_response_msg(RSLT_FAIL, msg)

# returns token, reason - where token is non-empty on success
# if set_req_user = True, then set req.user appropriately (on success)
def authenticate_user(req, user, password, set_req_user=False):
    # scan user files for matching user
    user_dir = req.config.data_dir + "/users"
    try:
        user_files = os.listdir( user_dir )
    except:
        msg = "Error: could not read user files from " + user_dir
        log_this(msg)
        return None, msg

    for ufile in user_files:
        if not ufile.startswith("user-"):
            continue
        upath = user_dir + "/" + ufile
        try:
            ufd = open(upath)
        except:
            log_this("Error opening upath %s" % upath)
            continue

        try:
            udata = json.load(ufd)
        except:
            ufd.close()
            log_this("Error reading json data from file %s" % upath)
            continue

        #log_this("in get_user: udata= %s" % udata)
        try:
            user_name = udata["name"]
        except:
            log_this("user file '%s' is missing 'name' field" % upath)
            continue

        if user != user_name:
            continue

        # found a match - check password
        user_password = udata.get("password", "")

        if password == user_password:
            log_this("Authenticated user '%s'" % user)
            token = udata.get("auth_token", "")
            if token:
                if set_req_user:
                    req.user.name = user_name
                    admin = udata.get("admin", "")
                    if admin == "True":
                        req.user.admin = True
                    else:
                        req.user.admin = False
                return (token, "")
            else:
                return (None, "Invalid token for user '%s' on server" % user)
        else:
            msg = "Password mismatch on login attempt for user '%s'" % user
            log_this(msg)

    return (None, "Authentication for user '%s' failed" % user)

# find a resource that applies to a particular board feature
# returns resource, reason - where resource is non-empty on success
# logs any errors encountered
def find_resource(req, board, feature):
    # scan resource files for a match
    res_dir = req.config.data_dir + "/resources"
    try:
        res_files = os.listdir( res_dir )
    except:
        msg = "Error: could not read resource files from " + res_dir
        log_this(msg)
        return None, msg

    for rfile in res_files:
        dlog_this("checking rfile %s" % rfile)
        if not rfile.startswith("resource-"):
            continue
        rpath = res_dir + "/" + rfile
        try:
            rfd = open(rpath)
        except:
            log_this("Error opening rpath %s" % rpath)
            continue

        try:
            rdata = json.load(rfd)
        except:
            rfd.close()
            log_this("Error reading json data from file %s" % rpath)
            continue

        dlog_this("in find_resource...: rdata= %s" % rdata)
        try:
            rboard = rdata["board"]
        except:
            dlog_this("resource file '%s' is missing 'board' field" % rpath)
            continue

        if board != rboard:
            continue

        # found a match - check board-endpoint against feature string
        board_feature = rdata.get("board_feature", "")

        dlog_this("feature=%s, board_feature=%s" % (feature, board_feature))

        if feature == board_feature:
            return (rdata["name"], None)

    return (None, "No match found for '%s:%s'" % (board, feature))

# api paths are:
#  lc/ebf command -> api path
# list boards, list devices -> api/v0.2/devices/"
# mydevices -> api/v0.2/devices/mine"
# {board} allocate -> api/v0.2/devices/{board}/assign
# {board} release -> api/v0.2/devices/{board}/release"
# {board} release force -> api/v0.2/devices/{board}/release"
# {board} status -> api/v0.2/devices/{board}
# {board} get_resource -> api/v0.2/devices/{board}/get_resource/{resource_type}
# {resource} pm start -> api/v0.2/resources/{resource}/power-measurement/start-capture
# {resource} pm stop -> api/v0.2/resources/{resource}/power-measurement/stop-capture/token
# {resource} pm get-data -> api/v0.2/resources/{resource}/power-measurement/get-data/token
# {resource} pm delete -> api/v0.2/resources/{resource}/power-measurement/delete/token
# {resource} serial start -> api/v0.2/resources/{resource}/serial/start-capture
# {resource} serial stop -> api/v0.2/resources/{resource}/serial/stop-capture/token
# {resource} serial get-data -> api/v0.2/resources/{resource}/serial/get-data/token
# {resource} serial delete -> api/v0.2/resources/{resource}/serial/delete/token
# {resource} serial put-data -> POST api/v0.2/resources/{resource}/serial/put-data
# {resource} serial set-config -> POST api/v0.2/resources/{resource}/serial/set-config
# {resource} audio start -> api/v0.2/resources/{resource}/audio/start-capture
# {resource} audio stop -> api/v0.2/resources/{resource}/audio/stop-capture/token
# {resource} audio get-data -> api/v0.2/resources/{resource}/audio/get-data/token
# {resource} audio delete -> api/v0.2/resources/{resource}/audio/delete/token
# {resource} audio put-data -> POST api/v0.2/resources/{resource}/audio/put-data
# {resource} audio set-config -> POST api/v0.2/resources/{resource}/audio/set-config

def do_api(req):
    dlog_this("in do_api")
    # determine api operation from path
    req_path = req.environ.get("PATH_INFO", "")
    log_this("in do_api: req_path=%s" % req_path)
    path_parts = req_path.split("/")
    # get the path elements after 'api'
    parts = path_parts[path_parts.index("api")+1:]

    #show_header(req, "in do_api")
    #req.html.append("parts=%s" % parts)
    dlog_this("parts=%s" % parts)

    # check API version.  Currently, we only support v0.2
    if parts[0] == "v0.2":
        del(parts[0])
    else:
        req.send_api_response_msg(RSLT_FAIL, "Unsupported api '%s'" % parts[0])

    if not parts:
        msg = "Invalid empty path after /api"
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

    # ignore trailing slash in API
    # eg: treat /api/v0.2/devices/ like /api/v0.2/devices
    if not parts[-1]:
        del(parts[-1])

    if parts[0] == "token":
        # return auth token for user (on successful authentication)
        #log_this("form.value=%s" % req.form.value)
        data = json.loads(req.form.value.decode("utf-8"))
        try:
            #log_this("data=%s" % data)
            user = data["username"]
            password = data["password"]
        except:
            msg = "Could not parse user/password data from form"
            log_this(msg)
            req.send_api_response_msg(RSLT_FAIL, msg)
            return

        token, reason = authenticate_user(req, user, password)
        if not token:
            req.send_api_response_msg(RSLT_FAIL, reason)
            return

        req.send_api_response(RSLT_OK, { "data": { "token": token } } )
        return

    if parts[0] == "devices":
        if len(parts) == 1:
            # handle /api/devices - list devices
            # list devices (boards)
            return_api_object_list(req, "board")
            return
        else:
            board = parts[1]
            if board == "mine":
                return_my_board_list(req)
                return

            if len(parts) == 2:
                # handle api/devices/{board}
                return_api_object_data(req, "board", board)
                return
            else:
                action = parts[2]
                rest = parts[3:]
                return_api_board_action(req, board, action, rest)
                return
        return
    elif parts[0] == "resources":
        if len(parts) == 1:
            # handle /api/resources - list resources
            # list devices
            return_api_object_list(req, "resource")
            return
        else:
            resource = parts[1]
            if len(parts) == 2:
                # handle api/resource/{resource}
                return_api_object_data(req, "resource", resource)
                return
            else:
                res_type = parts[2]
                rest = parts[3:]
                if res_type in ["power-measurement", "serial", "camera", "canbus", "audio"]:
                    return_api_resource_action(req, resource, res_type, rest)
                    return

                msg = "Unsupported elements '%s/%s' after /api/resources" % (res_type, "/".join(rest))
                req.send_api_response_msg(RSLT_FAIL, msg)
                return
    elif parts[0] == "requests":
        if len(parts) == 1:
            # handle /api/requests - list requests
            return_api_object_list(req, "request")
            return
        else:
            rest = parts[2:]
            msg = "Unsupported elements '%s' after /api/requests" % ("/".join(rest))
            req.send_api_response_msg(RSLT_FAIL, msg)
            return
    else:
        msg = "Unsupported element '%s' after /api/" % parts[0]
        req.send_api_response_msg(RSLT_FAIL, msg)
        return

def handle_request(environ, req):
    global debug
    global config_msg

    req.environ = environ

    dlog_this("in handle_request: debug flag is set")
    # uncomment this to debug configuration issues
    #dlog_this("config_msg=%s" % config_msg)

    # look up user, for those that pass an authorization token
    req.set_user()

    # debug request data
    if debug:
        log_env(req, CGI_VARS)
        #content_len = environ.get("CONTENT_LENGTH", 0)
        #if content_len and content_len < 1000:
        #    dlog_this("form.value=%s" % req.form.value)

    # determine action, if any
    try:
        action = req.form.getfirst("action", "show")
    except TypeError:
        action = "api"

    #reg.add_to_message('action="%s"' % action)
    #log_this('action="%s"' % action)
    req.action = action

    # get page name (last element of path)
    path_info = environ.get("PATH_INFO", "%s/Main" % req.config.url_base)
    if path_info.startswith(req.config.url_base):
        obj_path = path_info[len(req.config.url_base):]
    else:
        obj_path = path_info

    page_name = os.path.basename(obj_path)
    obj_type = os.path.dirname(obj_path)
    if not page_name:
        page_name = "Main"
    req.set_page_name(page_name)
    req.set_obj_type(obj_type)

    #uncomment these to debug path stuff
    #req.add_to_message("PATH_INFO=%s" % environ.get("PATH_INFO"))
    #req.add_to_message("path_info=%s" % path_info)
    #req.add_to_message("obj_path=%s" % obj_path)
    #req.add_to_message("page_name=%s" % page_name)

    # see if /api is in path
    if obj_path.startswith("/api"):
        action = "api"
        req.api_path = obj_path[4:]

    if obj_path.startswith("/raw"):
        action = "raw"
        req.obj_path = obj_path[4:]

    #req.add_to_message("action=%s" % action)

    # NOTE: uncomment this when you get a 500 error
    #show_header(req, 'Debug')
    #show_env(req, environ)
    #show_env(req, environ, True)
    log_this("in handle_request: action='%s', page_name='%s'" % (action, page_name))
    #req.add_to_message("in main request loop: action='%s'<br>" % action)

    # perform action
    # keep this for reference
    old_action_list = ["show", "api", "raw",
            "add_board", "add_resource", "put_request",
            "query_objects",
            "get_board", "get_resource", "get_request",
            "remove_request", "update_request",
            "put_log", "get_log",
            "user_edit_form", "create_user_form", "create_user"]

    action_list = ["show", "api", "raw",
            "manage_boards", "add_board_form", "add_board",
            "view_board_config",
            "edit_board_form", "update_board",
            "remove_board_confirm", "remove_board",
            "manage_resources", "add_resource_form", "add_resource",
            "view_resource_config",
            "edit_resource_form", "update_resource",
            "remove_resource_confirm", "remove_resource",
            "login_form", "login", "logout",
            "manage_users", "add_user_form", "add_user",
            "view_user_config",
            "edit_user_admin_form", "edit_user_user_form", "update_user",
            "remove_user_confirm", "remove_user"
            ]

    # these are actions that only admin users can perform
    admin_only_action_list = [ "raw",
            "manage_boards", "add_board_form", "add_board",
            "view_board_config",
            "edit_board_form", "update_board",
            "remove_board_confirm", "remove_board",
            "manage_resources", "add_resource_form", "add_resource",
            "view_resource_config",
            "edit_resource_form", "update_resource",
            "remove_resource_confirm", "remove_resource",
            "manage_users", "add_user_form", "add_user",
            "view_user_config",
            "edit_user_admin_form", "update_user",
            "remove_user_confirm", "remove_user"
            ]

    # map action names to "do_<action>" functions
    if action in action_list:
        try:
            action_function = globals().get("do_" + action)
        except:
            msg = "Error: unsupported action '%s' (probably missing a do_%s routine)" % (action, action)
            req.send_response(RSLT_FAIL, msg)
            return

        # check that we have admin permissions to perform the action
        if not req.user.admin and action in admin_only_action_list:
            msg = "Error: action '%s' requires Administrator privileges (which you do not have)" % action
            show_header(req, "Permission Failure")
            req.html.append(req.html_error(msg))
            req.show_footer()
            return

        action_function(req)

        if not req.footer_shown and action != "api":
            req.show_footer()
        return

    show_header(req, "LabControl server Error")
    req.html.append(req.html_error("Unknown action '%s'" % action))
    if not req.footer_shown:
        req.show_footer()

def cgi_main():
    #dlog_this("os.environ='%s'" % os.environ)
    #dlog_this("stdin='%s'" % sys.stdin.read())
    # handle json data myself, as the cgi module has a bug with
    # data submitted via the requests module as application/json
    if os.environ.get("CONTENT_TYPE", "") == "application/json":
        bin_data = sys.stdin.buffer.read()
        #dlog_this("incoming json form data='%s'" % data)
        form = mycgiform_class(bin_data)
    else:
        form = cgi.FieldStorage()

    req = req_class(config, form)
    req.is_cgi = True

    try:
        handle_request(os.environ, req)
    except SystemExit:
        pass
    except:
        show_header(req, "LabControl Server Error")
        req.html.append('<font color="red">Execution raised by software</font>')

        # show traceback information here:
        req.html.append("<pre>")
        import traceback
        (etype, evalue, etb) = sys.exc_info()
        tb_msg = traceback.format_exc()
        req.html.append("traceback=%s" % tb_msg)
        req.html.append("</pre>")
        log_this("LabControl Server Error")
        log_this("traceback=%s" % tb_msg)


    # output html to stdout
    for line in req.html:
        print(line)
        if debug_api_response:
            dlog_this(line)

    sys.stdout.flush()

if __name__=="__main__":
    cgi_main()
