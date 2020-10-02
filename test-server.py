"""CGI-savvy HTTP Server.

This module builds on SimpleHTTPServer by implementing GET and POST
requests to cgi-bin scripts.

If the os.fork() function is not present (e.g. on Windows),
os.popen2() is used as a fallback, with slightly altered semantics; if
that function is not present either (e.g. on Macintosh), only Python
scripts are supported, and they are executed by the current process.

In all cases, the implementation is intentionally naive -- all
requests are executed sychronously.

SECURITY WARNING: DON'T USE THIS CODE UNLESS YOU ARE INSIDE A FIREWALL
-- it may execute arbitrary Python code or external programs.

"""

__version__ = "0.4"

__all__ = ["CGIHTTPRequestHandler"]

import os, sys, urllib, select
import re
import BaseHTTPServer
import SimpleHTTPServer
import CGIHTTPServer
from urlparse import urlparse


class fServerRequestHandler(CGIHTTPServer.CGIHTTPRequestHandler):
    """CGIHTTPServer doesn't handle CGI scripts for do_GET
    (What's up with that?)
    """
    def do_GET(self):
        """Serve a GET request."""
        if self.is_cgi():
            self.log_message("is_cgi")
            self.run_cgi()
        else:
            self.log_message("is not cgi")
            f = self.send_head()
            if f:
                self.copyfile(f, self.wfile)
                f.close()
    def is_cgi(self):
        cgi_directories = ['/cgi-bin', '/htbin']

        """Test whether self.path corresponds to a CGI script.

        Return a tuple (dir, rest) if self.path requires running a
        CGI script, None if not.  Note that rest begins with a
        slash if it is not empty.

        The default implementation tests whether the path
        begins with one of the strings in the list
        self.cgi_directories (and the next character is a '/'
        or the end of the string).

        """

        full_url = self.path
	# escape embedded '%'s to avoid annoying exceptions
	path = re.sub("%","%%", full_url)
        self.log_message("path=%s" % path)

        for x in self.cgi_directories:
            i = len(x)
            if full_url[:i] == x and (not path[i:] or path[i] == '/'):
                self.cgi_info = path[:i], path[i+1:]
                return True

        # also check if path ends in .cgi or .py
        # parse path into parts: dirname, scriptname, query
        parts = urlparse(self.path)

        self.log_message("parts=%s" % str(parts))
        if parts.path.endswith(".py"):
            self.cgi_info = os.path.dirname(parts.path), os.path.basename(parts.path) + "?" + parts.query
            self.log_message("cgi_info=%s" % str(self.cgi_info))
            return True

        self.log_message("os.path.dirname(parts.path)=%s" % os.path.dirname(parts.path))
        # allow foo.py/PageName?query_name=query_value
        # that is, one level of path past the script name
        path_dir = os.path.dirname(parts.path)
        if path_dir.endswith(".py"):
            dirpart = os.path.dirname(path_dir)
            rest = parts.path[len(dirpart):]
            self.cgi_info = dirpart, rest + "?" + parts.query
            self.log_message("cgi_info=%s" % str(self.cgi_info))
            return True

        # interpret any path element ending in .py to be a script
        #self.log_message("os.path.dirname(parts.path)=%s" % os.path.dirname(parts.path))
        # allow foo.py/item1/item2?name=value
        path_dir = os.path.dirname(parts.path)
        dirpart = ""
        elements = path_dir.split("/")
        index = 0
        for element in elements:
            if element.endswith(".py"):
                rest = "/".join(elements[index:])
                self.cgi_info = dirpart, rest + "?" + parts.query
                self.log_message("cgi_info=%s" % str(self.cgi_info))
                return True
            else:
                dirpart += "/" + element
                index += 1
        return False

    def is_python(self, path):
        """Test whether argument path is a Python script."""
        head, tail = os.path.splitext(path)
        #self.log_message("extension=%s" % tail.lower())
        return tail.lower() in (".py", ".pyw")

    def run_cgi(self):
        """Execute a CGI script."""
        dir, rest = self.cgi_info
        i = rest.rfind('?')
        if i >= 0:
            # strip off query
            rest, query = rest[:i], rest[i+1:]
        else:
            query = ''
        i = rest.find('/')
        if i >= 0:
            script, rest = rest[:i], rest[i:]
        else:
            script, rest = rest, ''

        scriptname = script
        scriptfile = self.translate_path(scriptname)
        parts = urlparse(self.path)
        self.log_message("scriptfile=%s" % scriptfile)
        if not os.path.exists(scriptfile):
            self.send_error(404, "No such CGI script (%s)" % `scriptname`)
            return
        if not os.path.isfile(scriptfile):
            self.send_error(403, "CGI script is not a plain file (%s)" %
                            `scriptname`)
            return
        ispy = self.is_python(scriptname)
        self.log_message("ispy=%s" % ispy)

        if not ispy:
            if not (self.have_fork or self.have_popen2 or self.have_popen3):
                self.send_error(403, "CGI script is not a Python script (%s)" %
                                `scriptname`)
                return
            if not self.is_executable(scriptfile):
                self.send_error(403, "CGI script is not executable (%s)" %
                                `scriptname`)
                return

        # Reference: http://hoohoo.ncsa.uiuc.edu/cgi/env.html
        # XXX Much of the following could be prepared ahead of time!
        env = {}
        env['SERVER_SOFTWARE'] = self.version_string()
        env['SERVER_NAME'] = self.server.server_name
        env['GATEWAY_INTERFACE'] = 'CGI/1.1'
        env['SERVER_PROTOCOL'] = self.protocol_version
        env['SERVER_PORT'] = str(self.server.server_port)
        env['REQUEST_METHOD'] = self.command
        uqpath = urllib.unquote(parts.path)
        env['PATH_INFO'] = uqpath
        env['PATH_TRANSLATED'] = self.translate_path(uqpath)
        env['SCRIPT_NAME'] = scriptname
        if parts.query:
            env['QUERY_STRING'] = parts.query
        host = self.address_string()
        if host != self.client_address[0]:
            env['REMOTE_HOST'] = host
        env['REMOTE_ADDR'] = self.client_address[0]
        # XXX AUTH_TYPE
        # XXX REMOTE_USER
        # XXX REMOTE_IDENT
        if self.headers.typeheader is None:
            env['CONTENT_TYPE'] = self.headers.type
        else:
            env['CONTENT_TYPE'] = self.headers.typeheader
        length = self.headers.getheader('content-length')
        if length:
            env['CONTENT_LENGTH'] = length
        accept = []
        for line in self.headers.getallmatchingheaders('accept'):
            if line[:1] in "\t\n\r ":
                accept.append(line.strip())
            else:
                accept = accept + line[7:].split(',')
        env['HTTP_ACCEPT'] = ','.join(accept)
        ua = self.headers.getheader('user-agent')
        if ua:
            env['HTTP_USER_AGENT'] = ua
        co = filter(None, self.headers.getheaders('cookie'))
        if co:
            env['HTTP_COOKIE'] = ', '.join(co)
        # XXX Other HTTP_* headers
        # Since we're setting the env in the parent, provide empty
        # values to override previously set values
        for k in ('QUERY_STRING', 'REMOTE_HOST', 'CONTENT_LENGTH',
                  'HTTP_USER_AGENT', 'HTTP_COOKIE'):
            env.setdefault(k, "")
        os.environ.update(env)

        self.send_response(200, "Script output follows")

        decoded_query = query.replace('+', ' ')

        if self.have_fork:
            # Unix -- fork as we should
            args = [script]
            if '=' not in decoded_query:
                args.append(decoded_query)
	    # FIXTHIS - should setuid to reduce security risk!!
            #nobody = nobody_uid()
            self.wfile.flush() # Always flush before forking
            pid = os.fork()
            if pid != 0:
                # Parent
                pid, sts = os.waitpid(pid, 0)
                # throw away additional data [see bug #427345]
                while select.select([self.rfile], [], [], 0)[0]:
                    if not self.rfile.read(1):
                        break
                if sts:
                    self.log_error("CGI script exit status %#x", sts)
                return
            # Child
            try:
		# FIXTHIS - should setuid to reduce security risk!!
                #try:
                #    os.setuid(nobody)
                #except os.error:
                #    pass
                os.dup2(self.rfile.fileno(), 0)
                os.dup2(self.wfile.fileno(), 1)
                self.log_message("scriptfile: %s", scriptfile)
                os.execve(scriptfile, args, os.environ)
            except:
                self.server.handle_error(self.request, self.client_address)
                os._exit(127)

        elif self.have_popen2 or self.have_popen3:
            # Windows -- use popen2 or popen3 to create a subprocess
            import shutil
            if self.have_popen3:
                popenx = os.popen3
            else:
                popenx = os.popen2
            cmdline = scriptfile
            if self.is_python(scriptfile):
                interp = sys.executable
                if interp.lower().endswith("w.exe"):
                    # On Windows, use python.exe, not pythonw.exe
                    interp = interp[:-5] + interp[-4:]
                cmdline = '%s -u "%s"' % (interp, cmdline)
            if '=' not in query and '"' not in query:
                cmdline = '%s "%s"' % (cmdline, query)
            self.log_message("command: %s", cmdline)
            try:
                nbytes = int(length)
            except (TypeError, ValueError):
                nbytes = 0
            files = popenx(cmdline, 'b')
            fi = files[0]
            fo = files[1]
            if self.have_popen3:
                fe = files[2]
            if self.command.lower() == "post" and nbytes > 0:
                data = self.rfile.read(nbytes)
                fi.write(data)
            # throw away additional data [see bug #427345]
            while select.select([self.rfile._sock], [], [], 0)[0]:
                if not self.rfile._sock.recv(1):
                    break
            fi.close()
            shutil.copyfileobj(fo, self.wfile)
            if self.have_popen3:
                errors = fe.read()
                fe.close()
                if errors:
                    self.log_error('%s', errors)
            sts = fo.close()
            if sts:
                self.log_error("CGI script exit status %#x", sts)
            else:
                self.log_message("CGI script exited OK")

        else:
            # Other O.S. -- execute script in this process
            save_argv = sys.argv
            save_stdin = sys.stdin
            save_stdout = sys.stdout
            save_stderr = sys.stderr
            try:
                try:
                    sys.argv = [scriptfile]
                    if '=' not in decoded_query:
                        sys.argv.append(decoded_query)
                    sys.stdout = self.wfile
                    sys.stdin = self.rfile
                    execfile(scriptfile, {"__name__": "__main__"})
                finally:
                    sys.argv = save_argv
                    sys.stdin = save_stdin
                    sys.stdout = save_stdout
                    sys.stderr = save_stderr
            except SystemExit, sts:
                self.log_error("CGI script exit status %s", str(sts))
            else:
                self.log_message("CGI script exited OK")

def test(HandlerClass = fServerRequestHandler,
         ServerClass = BaseHTTPServer.HTTPServer):
    SimpleHTTPServer.test(HandlerClass, ServerClass)


if __name__ == '__main__':
    test()
