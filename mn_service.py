"""
Module contains RequestHandler classes for Desktop / Mobile App interactions
"""
__author__ = 'morozov'
from tornado.ioloop import IOLoop
from tornado.web import RequestHandler, HTTPError
from tornado.log import access_log, app_log, gen_log
import tornado.httputil
from tornado.options import define, options
from tornado.iostream import StreamClosedError
import sys
import os
import mn_httpserver as http
import mynotes as mn
import mn_instance as instance


class MN_Handler(RequestHandler):
    #   Base class for Desktop/Mobile App handlers
    def __init__(self, application, request, **kwargs):
        RequestHandler.__init__(self, application, request, **kwargs)
        _headers = self.request.headers
        self.ProductID = _headers.get(mn.MN_PRODUCT_ID,'')
        self.RequestID = _headers.get(mn.MN_REQUEST_ID,'')
        self._timeout = None
        self._instance = application._instance

    def finish(self, chunk=None):
        """Finishes this response, ending the HTTP request.
        !!!! pass AssertionError if request is closed"""
        if self._finished:
            raise RuntimeError("finish() called twice.  May be caused "
                               "by using async operations without the "
                               "@asynchronous decorator.")

        if chunk is not None:
            self.write(chunk)

        # Automatically support ETags and add the Content-Length header if
        # we have not flushed any content yet.
        if not self._headers_written:
            if (self._status_code == 200 and
                self.request.method in ("GET", "HEAD") and
                "Etag" not in self._headers):
                etag = self.compute_etag()
                if etag is not None:
                    self.set_header("Etag", etag)
                    inm = self.request.headers.get("If-None-Match")
                    if inm and inm.find(etag) != -1:
                        self._write_buffer = []
                        self.set_status(304)
            if self._status_code == 304:
                assert not self._write_buffer, "Cannot send body with 304"
                self._clear_headers_for_304()
            elif "Content-Length" not in self._headers:
                content_length = sum(len(part) for part in self._write_buffer)
                self.set_header("Content-Length", content_length)

        if hasattr(self.request, "connection"):
            # Now that the request is finished, clear the callback we
            # set on the IOStream (which would otherwise prevent the
            # garbage collection of the RequestHandler when there
            # are keepalive connections)
            self.request.connection.stream.set_close_callback(None)

        if not self.application._wsgi:
            # enhancement is here:
            try:
                self.flush(include_footers=True)
                self.request.finish()
            except AssertionError:
                pass
            self._log()
        self._finished = True
        self.on_finish()

    def _request_summary(self):
        #   class specific summary
        _h = self.request.headers
        _h.get(mn.MN_RESPONSE_CODE,'--')
        remote_ip = _h.get('X-Real-IP',self.request.remote_ip)
        res = "(%s) %s %s [%s(%s)] (%s)" % \
              (_h.get(mn.MN_RESPONSE_CODE,'--'),
               self.request.method,
               self.request.uri,
               getattr(self,'ProductID','--') or '--',
               getattr(self,'RequestID','--') or '--',
               _h.get('X-Real-IP', _h.get('X-Forwarded-For', self.request.remote_ip)))
        return res + self._headers.get(mn.MN_ERROR_MESSAGE,'')

    def _handle_request_exception(self, e):
        #   If something goes wrong at one side of interaction,
        #   closes the other side as well (desktop / app)
        _partner = None
        _interaction = mn.get_Interaction(self.RequestID)
        if _interaction and _interaction._source:
            _interaction._completed = False
            if _interaction._source == self:
                _partner = _interaction._destination
            else:
                _partner = _interaction._source
        #super(IWP_Handler, self)._handle_request_exception(e)
        if isinstance(e, HTTPError):
            if e.log_message:
                format = "%d %s: " + e.log_message
                args = [e.status_code, self._request_summary()] + list(e.args)
                gen_log.warning(format, *args)
                if e.status_code not in tornado.httputil.responses and not e.reason:
                    gen_log.error("Bad HTTP status code: %d", e.status_code)
                    self.send_error(500, exc_info=sys.exc_info())
                else:
                    self.send_error(e.status_code, exc_info=sys.exc_info())
        elif isinstance(e, StreamClosedError):
            if _partner:
                IOLoop.instance().add_callback (_partner.send_error, 503, exc_info=sys.exc_info())
                self.send_error(503, exc_info=sys.exc_info())
            else:
                self.send_error(500, exc_info=sys.exc_info())
        elif isinstance(e, http.StreamClosedWarning):
            app_log.warning('%s write to closed stream', self._request_summary())
            if _partner:
                IOLoop.instance().add_callback (_partner.send_error, 503, exc_info=sys.exc_info())
                self.send_error(503, exc_info=sys.exc_info())
            else:
                self.send_error(500, exc_info=sys.exc_info())
            pass
        else:
            app_log.error("Uncaught exception %s\n%r", self._request_summary(),self.request, exc_info=True)
            self.send_error(500, exc_info=sys.exc_info())

    def get_error_html(self, status_code, **kwargs):
        #   adds error message header instead of error page
        reason = ''
        if 'exception' in kwargs:
            reason = str(kwargs['exception'])
        self.add_header(mn.MN_ERROR_MESSAGE, reason)
        return ''

    def process_agent(self):
        #   Starts interaction with cached app (if any)
        #   waits for app otherwise
        #   (Desktop .... <-> Mobile App)
        _client = mn.get_Client(self.ProductID)
        if _client:
            mn.Interaction(_client, self)(_client, self)
        else:
            self._add_wait()
            self._timeout = IOLoop.instance().add_timeout(mn.MN_AGENT_TIMEOUT,self._response_no_client)

    def process_reply(self):
        #   Replies: Mobile App <- Desktop
        _interaction = mn.get_Interaction(self.RequestID, validateID = self.ProductID)

        if _interaction and not _interaction.client._closed() and not _interaction.agent:
            _interaction._set_agent(self)
            _interaction(self,_interaction.client, source_finish=True)
        elif _interaction and _interaction.agent:
            self.request.connection.no_keep_alive = True
            self.send_error(501,exc_info = (HTTPError, HTTPError(501, 'RequestID is being replied')))
        else:
            self.request.connection.no_keep_alive = True
            self.send_error(502,exc_info = (HTTPError, HTTPError(502, 'No client to reply')))

    def _response_no_client(self):
        self._remove_wait()
        self.add_header(mn.MN_RESPONSE_TYPE, mn.MN_NO_CLIENT)
        self.finish()

    def _response_no_agent(self):
        self._remove_client()
        self.add_header(mn.MN_RESPONSE_TYPE, mn.MN_NO_AGENT)
        self.finish()

    def _response_no_reply(self):
        if self._closed():
            reason = 'Request closed'
        else:
            reason = 'No reply from agent'
        self.add_header(mn.MN_RECYCLED, mn.MN_NO_REPLY)
        self.send_error(504, exc_info = (HTTPError,HTTPError(504,reason)))

    def _clear_transaction(self):
        _interaction = mn.get_Interaction(self.RequestID)
        if _interaction:
            _interaction._clear_transaction()

    def _add_wait(self):
        mn.add_wait(self)

    def _remove_wait(self):
        mn.remove_wait(self)

    def _add_client(self):
        mn.add_client(self)

    def _remove_client(self):
        mn.remove_client(self)

    def _add_cache(self):
        mn.add_cache(self.ProductID)

    def _closed(self):
        return self.request.connection.stream.closed()

    def _check_closed(self):
        return self.request.connection.stream._check_closed()


class Agent_ready(MN_Handler):
    #   Desktop is ready to listen Mobile App requests
    @tornado.web.asynchronous
    def post(self):
        self.process_agent()

    def on_finish(self):
        _interaction = mn.get_Interaction(self.RequestID)
        if _interaction:
            _interaction._clear_transaction()


class Agent_reply(MN_Handler):
    # Desktop replies to Mobile App request
    @tornado.web.asynchronous
    def post(self):
        self.process_reply()


class Client(MN_Handler, instance.MN_Instance_Handler):
    #   Mobile App connects to its Desktop (if any) to start transaction
    def __init__(self, application, request, **kwargs):
        MN_Handler.__init__(self, application, request, **kwargs)
        self.mn_server = ''
        self.mn_port = ''
        self._search_count = 2
        self._not_found_callback = self._response_no_agent

    def process_client(self, repeat = True):
        #   Starts transaction with cached Desktop (if any);
        #   waits if Desktop is to be appeared soon;
        #   refers to new Desktop location if found;
        _agent = None
        if not repeat:
            _agent = mn.get_Agent(self.ProductID)
        if _agent:
            mn.Interaction(self,_agent)(self,_agent)
        elif not self._closed() and self._instance._isHere(self.ProductID) and mn.get_cache(self.ProductID):
            if not repeat:
                self._add_client()
            self._timeout = IOLoop.instance().add_timeout(mn.MN_CLIENT_TIMEOUT, self.process_client)
        elif self._closed() or self._instance._isHere(self.ProductID):
            self._response_no_agent()
        else:
            self._find_desktop()

    @tornado.web.asynchronous
    def post(self):
        self.process_client(repeat=False)

    def on_finish(self):
        _interaction = mn.get_Interaction(self.RequestID, remove=True)
        if _interaction:
            _interaction._clear(self.request.request_time()*1000)


class Agent_ping (MN_Handler):
    # just 'ping' to choose the closest server
    @tornado.web.asynchronous
    def post(self):
        self.finish()


define('port', default='80')
define('port_ssl', default='443')
define('host', default='', type=str)
define('certfile', default=None, type=str)
define('keyfile', default=None, type=str)

define('server')
define('master', default=None, type=tuple)
define('range_file', default='range.ini')
define('range_size', default=1000)
define('master_range', default=None)
define('sites', default=[], type=list)
define('http_max_clients', default=10)

def _set_max_clients():
    if options.http_max_clients:
        fake_client = instance.tornado.httpclient.AsyncHTTPClient(max_clients=options.http_max_clients)
        del fake_client

tornado.options.add_parse_callback(_set_max_clients)

if __name__ == "__main__":
    instance_conf = None
    tornado.options.parse_command_line(final=False)
    if options.port:
        instance_conf = os.path.extsep.join(((os.path.join('instance', str(options.port))),'conf'))

    tornado.options.parse_config_file("mynotes.conf", final=bool(not instance_conf))

    if instance_conf and os.access(instance_conf, os.F_OK):
        tornado.options.parse_config_file(instance_conf, final=False)
    tornado.options.parse_command_line()


    application = http.Application_mn([
        (r"/ping", Agent_ping),
        (r"/hello", instance.inst_Hello),
        (r"/connected", instance.inst_Connected),
        (r"/range", instance.inst_Range),
        (r"/getuniversalid", instance.agent_getID),
        (r"/connect", instance.agent_Connect),
        (r"/client", instance.app_Client),
        (r"/hello/.*", instance.inst_Hello_port),
        (r"/connected/.*", instance.inst_Connected_port),
        (r"/range/.*", instance.inst_Range_port),
        (r"/find.*", instance.inst_Find),
        (r"/client/.*", Client),
        (r"/agentreply/.*", Agent_reply),
        (r"/agent/.*", Agent_ready)],
        instance=instance.mn_instance(
            options.server, options.port,
            master_instance=options.master,
            range_file=options.range_file,
            range_size=options.range_size,
            master_range=options.master_range,
            known_instances=options.sites
        )
    )

    http_server = http.HTTPServer_mn(application)
    http_server.listen(int(options.port), options.host)

    if options.certfile and options.keyfile and options.port_ssl:
        ssl_options={
            "certfile": options.certfile,
            "keyfile": options.keyfile,
            }
        https_server = http.HTTPServer_mn(application, ssl_options=ssl_options)
        https_server.listen(options.port_ssl, options.host)

    tornado.ioloop.IOLoop.instance().start()