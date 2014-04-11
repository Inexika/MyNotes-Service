""" Modified back-end classes to overrides some methods in Tornado classes:
IOStream_np, HTTPConnection_np, HTTPServer_np, Application_np"""
from __future__ import absolute_import, division, print_function, with_statement

import socket
from tornado import httputil

from tornado.httpserver import HTTPServer, HTTPRequest, HTTPConnection
from tornado.httpserver import _BadRequestException, gen_log
from tornado.web import Application

from tornado.tcpserver import ssl
from tornado.iostream import IOStream, SSLIOStream, _merge_prefix
from errno import ECONNABORTED, ECONNRESET, EWOULDBLOCK, EAGAIN
from tornado.netutil import ssl_wrap_socket
from tornado.log import app_log

from tornado.escape import native_str
from tornado import stack_context
import functools

from tornado.concurrent import Future


def asynchronous(method):
    """Wrap request handler methods with this if they are asynchronous.

    If this decorator is given, the response is not finished when the
    method returns. It is up to the request handler to call
    `self.finish() <RequestHandler.finish>` to finish the HTTP
    request. Without this decorator, the request is automatically
    finished when the ``get()`` or ``post()`` method returns. Example::

       class MyRequestHandler(web.RequestHandler):
           @web.asynchronous
           def get(self):
              http = httpclient.AsyncHTTPClient()
              http.fetch("http://friendfeed.com/", self._on_download)

           def _on_download(self, response):
              self.write("Downloaded!")
              self.finish()

    """
    # Delay the IOLoop import because it's not available on app engine.
    from tornado.ioloop import IOLoop
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self.application._wsgi:
            raise Exception("@asynchronous is not supported for WSGI apps")
        self._auto_finish = False
        with stack_context.ExceptionStackContext(
            self._stack_context_handle_exception):
            result = method(self, *args, **kwargs)
            if isinstance(result, Future):
                # If @asynchronous is used with @gen.coroutine, (but
                # not @gen.engine), we can automatically finish the
                # request when the future resolves.  Additionally,
                # the Future will swallow any exceptions so we need
                # to throw them back out to the stack context to finish
                # the request.
                def future_complete(f):
                    f.result()
                    if self._auto_finish and not self._finished:
                        self.finish()
                IOLoop.current().add_future(result, future_complete)
            return result
    return wrapper


class IOStream_mn(IOStream):
    #   Uses small limited buffer to read data portions one by one;
    #   reads until buffer is full;
    #   removes handler if _handle_read() occurs for stream with already full buffer
    def __init__(self, socket, *args, **kwargs):
        self.read_buffer_full = False
        super(IOStream_mn, self).__init__(socket, *args, **kwargs)

    def _read_to_buffer(self):
        #   Reads from the socket and appends the result to the read buffer.
        #   Returns the number of bytes read.  Returns 0 if there is nothing
        #   to read (i.e. the read returns EWOULDBLOCK or equivalent)
        #   !! OR (override) if self.max_buffer_size is to be exceeded.
        #                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        #   On error closes the socket and raises an exception.

        #   check .max_buffer_size
        self.read_buffer_full = self._read_buffer_size + self.read_chunk_size > self.max_buffer_size
        if self.read_buffer_full:
            return 0
        # --------------------------
        try:
            chunk = self.read_from_fd()
        except (socket.error, IOError, OSError) as e:
            if e.args[0] == ECONNRESET:
                self.close(exc_info=True)
                return
            self.close(exc_info=True)
            raise
        if chunk is None:
            return 0
        self._read_buffer.append(chunk)
        self._read_buffer_size += len(chunk)
        #actually should not happen, but... just in case
        if self._read_buffer_size > self.max_buffer_size:
            gen_log.error("Reached maximum read buffer size")
            self.close()
            raise IOError("Reached maximum read buffer size")
        return len(chunk)

    def _handle_events(self, fd, events):
        #   Temporary removes handler if _handle_read() occurs stream with full buffer
        if self.closed():
            gen_log.warning("Got events for closed stream %d", fd)
            return
        try:
            if events & self.io_loop.READ:
                self._handle_read()
            if self.closed():
                return
            if events & self.io_loop.WRITE:
                if self._connecting:
                    self._handle_connect()
                self._handle_write()
            if self.closed():
                return
            if events & self.io_loop.ERROR:
                self.error = self.get_fd_error()
                # We may have queued up a user callback in _handle_read or
                # _handle_write, so don't close the IOStream until those
                # callbacks have had a chance to run.
                self.io_loop.add_callback(self.close)
                return
            state = self.io_loop.ERROR
            if self.reading():
                state |= self.io_loop.READ
            if self.writing():
                state |= self.io_loop.WRITE
            if state == self.io_loop.ERROR:
                state |= self.io_loop.READ
                # -- removes handler if buffer is already full
                if self.read_buffer_full:
                    state=None
            if state is None:
                self._state = state
                self.io_loop.remove_handler(self.fileno())
                # gen_log.warning('%d handler has been removed',self.fileno())
                # -- handler is to be added back in run_callback (tornado code)
            elif state != self._state:
                assert self._state is not None,\
                "shouldn't happen: _handle_events without self._state"
                self._state = state
                self.io_loop.update_handler(self.fileno(), self._state)
        except Exception:
            gen_log.error("Uncaught exception, closing connection.",
                exc_info=True)
            self.close(exc_info=True)
            raise


class SSLIOStream_mn(SSLIOStream):
    #   SSL-connection is handled by reverse-proxy server now (nginx).
    #   No longer needs to have special class here.
    pass


class StreamClosedWarning(IOError):
    pass


class HTTPServer_mn ( HTTPServer ):
    #   Uses class with overridden methods instead of standard Tornado ones
    def handle_stream(self, stream, address):
        #   Uses HTTPConnection_mn instead
        no_keep_alive = True
        if issubclass(type(stream), SSLIOStream):
            protocol = 'https'
        else:
            protocol = 'http'
        HTTPConnection_mn(stream, address, self.request_callback,
            no_keep_alive, self.xheaders, protocol)

    def _handle_connection(self, connection, address):
        #   Uses IOStream_mn/SSLIOStream_mn with small buffer instead
        if self.ssl_options is not None:
            assert ssl, "Python 2.6+ and OpenSSL required for SSL"
            try:
                connection = ssl_wrap_socket(connection,
                    self.ssl_options,
                    server_side=True,
                    do_handshake_on_connect=False)
            except ssl.SSLError as err:
                if err.args[0] == ssl.SSL_ERROR_EOF:
                    return connection.close()
                else:
                    raise
            except socket.error as err:
                if err.args[0] == ECONNABORTED:
                    return connection.close()
                else:
                    raise
        try:
            if self.ssl_options is not None:
                stream = SSLIOStream_mn(connection, io_loop=self.io_loop, max_buffer_size=1024*64)
            else:
                stream = IOStream_mn(connection, io_loop=self.io_loop, max_buffer_size=1024*64)
            self.handle_stream(stream, address)
        except Exception:
            app_log.error("Error in connection callback", exc_info=True)


class HTTPConnection_mn (HTTPConnection):
    #   No "Content-Length too long" exception;
    #   Reads headers only, body is to be read later.
    def _on_headers(self, data):
        #   Request body is not read here
        try:
            data = native_str(data.decode('latin1'))
            eol = data.find("\r\n")
            start_line = data[:eol]
            try:
                method, uri, version = start_line.split(" ")
            except ValueError:
                raise _BadRequestException("Malformed HTTP request line")
            if not version.startswith("HTTP/"):
                raise _BadRequestException("Malformed HTTP version in HTTP Request-Line")
            headers = httputil.HTTPHeaders.parse(data[eol:])

            # HTTPRequest wants an IP, not a full socket address
            if self.address_family in (socket.AF_INET, socket.AF_INET6):
                remote_ip = self.address[0]
            else:
                # Unix (or other) socket; fake the remote address
                remote_ip = '0.0.0.0'

            self._request = HTTPRequest(
                connection=self, method=method, uri=uri, version=version,
                headers=headers, remote_ip=remote_ip, protocol=self.protocol)

            content_length = headers.get("Content-Length")
            if content_length:
                content_length = int(content_length)
                if content_length > self.stream.max_buffer_size:
                    pass
                if headers.get("Expect") == "100-continue":
                    self.stream.write(b"HTTP/1.1 100 (Continue)\r\n\r\n")

            self.request_callback(self._request)
        except _BadRequestException as e:
            gen_log.info("Malformed HTTP request from %s: %s",
                self.address[0], e)
            self.close()
            return

    def write(self, chunk, callback=None, error_on_closed=False):
        #   Writes a chunk of output to the stream. Warning if it's closed
        assert self._request, "Request closed"
        if not self.stream.closed():
            self._write_callback = stack_context.wrap(callback)
            self.stream.write(chunk, self._on_write_complete)
        elif chunk:
            app_log.warning('Write to closed stream "%s"[%i]' % (self._request.uri, len(chunk)))
            raise StreamClosedWarning('Write to closed stream "%s"[%i]' % (self._request.uri,len(chunk)))


class Application_mn (Application):
    #   Sets link to instance object in addition
    def __init__(self, handlers=None, default_host="", transforms=None, wsgi=False, **settings):
        Application.__init__(self, handlers, default_host, transforms, wsgi, **settings)
        if settings.get("instance"):
            self._instance = settings.get("instance")






