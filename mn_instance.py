"""Cloud service consists of instances, run on the servers.
Servers' names like eu.mynotesapp.com, us.mynotesapp.com are hardcoded.
More than one instances can be run on each server.
One of instance is Master one. It controls CustomerID ranges.
Instances are able to:
notify each other about their appearance,
ask additional CustomerID range (if needed),
inform when CustomerID desktop is connected to them,
...
"""
__author__ = 'morozov'

import tornado.httpclient
import os
from tornado.web import RequestHandler
from tornado.log import access_log, app_log, gen_log
from mynotes import MN_PRODUCT_ID, MN_RESPONSE_TYPE, MN_NO_AGENT

MN_INSTANCE_SERVER = "X-IWP-Host"
MN_INSTANCE_PORT = "X-IWP-Port"
MN_KNOWN_SERVERS = 'X-IWP-Hosts'
MN_KNOWN_PORTS = 'X-IWP-Ports'
MN_INSTANCE_RANGE_FROM = "X-IWP-Range-From"
MN_INSTANCE_RANGE_TO = "X-IWP-Range-To"
MN_INSTANCE_RANGE_SIZE = "X-IWP-Range-Size"
MN_TARGET_SERVER = "X-IWP-Target-Host"
MN_TARGET_PORT = "X-IWP-Target-Port"


class mn_instance():
    """Cloud service instance:
    server          - DNS-name server where instance runs
    port            - listening port
    master_instance - master instance (server,port)
    range_file      - file contained CustomerID range instance bounds
    range_size      - range size requested
    master_range    - file contained CustomerID master range to be distributed among others instances
    known_instances - [(server,port),...] default instances list
    """
    def __init__(self, server, port, master_instance, range_file, range_size, master_range=None,
                 known_instances=None):
        self.server = server
        self.port = port
        self.master_server = master_instance[0]
        self.master_port = master_instance[1]
        self.master =  (self.server==self.master_server and self.port==self.master_port)
        self.master_range = master_range
        assert (self.master and self.master_range) or not (self.master or self.master_range)

        assert range_file
        self.range_file = range_file
        self._range_fd = None
        self._range_requests = None
        self._need_range = False
        self.range_size = range_size

        self._servers = []
        self._instances = []
        self._IDs = {}
        self._initialized = False
        self._hello_headers = {MN_INSTANCE_SERVER:self.server, MN_INSTANCE_PORT:self.port}
        self._hello_awaiting =0

        if self.master:
            _master_range = self._range_from_file(self.master_range)
            assert _master_range and 0<=_master_range[0]<_master_range[1]

        if known_instances and self.server and self.port:
            for (_server, _port) in known_instances:
                if _server not in self._servers and _server!= self.server:
                    self._servers.append(_server)
                if _server == self.server and _port not in self._instances and _port!=self.port:
                    self._instances.append(_port)

        for _server in self._servers:
            self.hello(server = _server)
        for _port in self._instances:
            self.hello(port = _port)

        _range = self._range_from_file(self.range_file, create_if_no=True)
        if not _range or not (0<_range[0]<=_range[1]):
            self._range_requests = [self._servers[:], self._instances[:]]
            self._get_range(self.master_server, self.master_port, self._response_got_range, self.range_size)

    def hello(self, server = None, port = None, instance_headers = None):
        """Handshake requests to neighbor instances and servers of others
        """
        url, _headers = self._url('hello', server, port)
        access_log.debug("hello: %s" % url)
        if instance_headers is None:
            _headers.update(self._hello_headers)
        else:
            _headers.update(instance_headers)
        request=tornado.httpclient.HTTPRequest(url, body='', method="POST", use_gzip=False, headers=_headers)
        http_client = tornado.httpclient.AsyncHTTPClient()
        self._hello_awaiting +=1
        http_client.fetch(request, self._response_hello)

    def connected(self, CustomerID, server=None, port=None, instance_headers = None):
        """Informs others CustomerID Desktop is connected to the instance
        """
        url, _headers = self._url('connected',server, port)
        _headers.update({MN_PRODUCT_ID: CustomerID})
        if instance_headers is None:
            _headers.update(self._hello_headers)
        else:
            _headers.update(instance_headers)

        request=tornado.httpclient.HTTPRequest(url, body='', method="POST", use_gzip=False, headers=_headers)
        http_client = tornado.httpclient.AsyncHTTPClient()
        http_client.fetch(request, self._response_connected)

    def _find(self, CustomerID, server=None, port=None, callback=None):
        url,_headers = self._url('find',server, port)
        _headers.update({MN_PRODUCT_ID: CustomerID})
        request=tornado.httpclient.HTTPRequest(url, body='', method="POST", use_gzip=False, headers=_headers)
        http_client = tornado.httpclient.AsyncHTTPClient()
        http_client.fetch(request, callback)

    def _get_range(self, server=None, port=None, callback=None, num=None):
        url, _headers = self._url('range',server, port)
        _headers.update(self._hello_headers)
        if num:
            _headers.update({MN_INSTANCE_RANGE_SIZE: str(num)})
        request=tornado.httpclient.HTTPRequest(url, body='', method="POST", use_gzip=False, headers=_headers)
        http_client = tornado.httpclient.AsyncHTTPClient()
        http_client.fetch(request, callback)

    def _range_from_range(self, auto=True, num=None, fraction=0.1):
        _range = None
        if auto and self.master:
            _file = self.master_range
        else:
            _file = self.range_file

        _from, _to = self._range_from_file(_file)

        if num and _to - _from - int(num) + 2:
            _num = int(num)
        elif fraction<1:
            _num = int((_to-_from+1)*fraction)
        else:
            _num = 0
        if _num:
            _range = (str(_from),str(_from + _num -1))
            if _to -_from -_num + 1:
                self._range_to_file(_file, (str(_from + _num),str(_to)))
            else:
                self._range_to_file(_file, (str(0),str(0)))
        else:
            self._range_fd.close()
        self._range_fd = None
        if (_num == 0 or _to-_from-_num+1 == 0) and not (auto and self.master):
            self._need_range = True

        return _range

    def _range_from_file(self, range_file, create_if_no=False):
        res = None
        fl = range_file
        sep = range_file.rpartition(os.sep)
        if sep[2]:
            path=sep[0]+sep[1]
        else:
            path=os.curdir

        if os.access(fl, os.F_OK | os.R_OK| os.W_OK):
            mode='r'
        elif not os.access(fl, os.F_OK) and create_if_no and os.access(path, os.W_OK):
            mode='w'
            fd = open(fl, mode)
            fd.close()
        else:
            raise EnvironmentError, "You don't have enough permissions to create or open file '%s'" % fl

        if os.path.getsize(fl):
            fd = open(fl, mode)
            try:
                res = (int(fd.readline()), int(fd.readline()))
            except (ValueError,EOFError):
                raise EnvironmentError, "File '%s' contains incorrect or missing data" % fl
            finally:
                fd.close()
        else:
            res = (0,0)
        return res

    def _range_to_file(self, range_file, _range):
        access_log.debug("range_to_file (range=%s)" % str(_range))
        fl = range_file
        if os.access(fl, os.F_OK | os.R_OK | os.W_OK):
            fd = open(fl, 'w')
        else:
            raise EnvironmentError, "File '%s' doesn't exist or you don't have enough permissions" % fl
        try:
            fd.write('\n'.join(_range))
        finally:
            fd.close()

    def _getLocation(self, CustomerID):
        if CustomerID in self._IDs:
            return self._IDs[CustomerID]
        else:
            return None

    def _updateLocation(self, CustomerID, instance=None):
        if instance:
            _instance=instance
        else:
            _instance=(self.server, self.port)
        self._IDs.update({CustomerID:_instance})

    def _isHere(self, CustomerID):
        return self._getLocation(CustomerID) == (self.server, self.port)

    def _response_hello(self, response):
        self._hello_awaiting -=1
        if response.error:
            self._rem_instance(response.request)
        else:
            _headers = response.headers
            if MN_KNOWN_SERVERS in _headers:
                for _server in _headers[MN_KNOWN_SERVERS].split(','):
                    if _server and _server not in self._servers and _server!=self.server:
                        self._servers.append(_server)
                        self.hello(server = _server)
            if MN_KNOWN_PORTS in _headers:
                for _port in _headers[MN_KNOWN_PORTS].split(','):
                    if _port and _port not in self._instances and _port!=self.port:
                        self._instances.append(_port)
                        self.hello(port = _port)

        if self._hello_awaiting == 0:
            self._initialized = True

    def _response_connected(self, response):
        if response.error:
            self._rem_instance(response.request)

    def _response_got_range(self, response):
        if MN_INSTANCE_RANGE_FROM in response.headers and MN_INSTANCE_RANGE_TO in response.headers:
            _got_range = (response.headers[MN_INSTANCE_RANGE_FROM], response.headers[MN_INSTANCE_RANGE_TO])
            self._range_to_file(self.range_file, _got_range)
            self._need_range = False
        else:
            params = {'callback':self._response_got_range}
            if self._range_requests[0]:
                params.update({'server':self._range_requests[0].pop()})
            elif self._range_requests[1]:
                params.update({'port':self._range_requests[1].pop()})
            else:
                self._range_requests = None
                return
            self._get_range(**params)

    def _add_instance(self, server, port):
        _added=False
        if port and port not in self._instances and server==self.server and port!=self.port:
            self._instances.append(port)
            _added=True
        if server and server!=self.server and server not in self._servers:
            self._servers.append(server)
            _added=True
        if _added:
            access_log.debug('add instance: server=%s, port=%s' % (server,port))


    def _rem_instance(self, request):
        headers=request.headers
        _server=headers.get(MN_TARGET_SERVER,'')
        _port=headers.get(MN_TARGET_PORT,'')
        access_log.debug('remove instance: server=%s, port=%s' % (_server,_port))
        if _server and _port:
            return
        elif _server:
            self._servers.remove(_server)
        elif _port:
            self._instances.remove(_port)


    def _url(self, path, server = None, port = None):
        headers={}
        if not server:
            _server = self.server
        else:
            _server = server
            headers[MN_TARGET_SERVER]=server

        if not _server.startswith(('http://','https://')):
            _server = 'http://%s' % _server
        if port:
            url= '/'.join ((_server, path, str(port)))
            headers[MN_TARGET_PORT] = port
        else:
            url= '/'.join ((_server, path))

        return url, headers

    def _url_localhost(self, path, server = None, port = None):
        """_url() not-tested-yet version
        refer to instances through the localhost instead their DNS names"""
        headers={}
        if port and (not server or server==self.server):
            #via localhost if refers to 'its' instance
            url = 'http://localhost:%s/%s' % (str(port),path)
            headers[MN_TARGET_PORT] = port
            return url, headers

        elif not server:
            #refers to the own server w/o port specified
            #not sure if effective, just in case...
            _server = self.server
        else:
            _server = server
            headers[MN_TARGET_SERVER]=server

        if not _server.startswith(('http://','https://')):
            _server = 'http://%s' % _server
        if port:
            url= '/'.join ((_server, path, str(port)))
            headers[MN_TARGET_PORT] = port
        else:
            url= '/'.join ((_server, path))

        return url, headers


class MN_Instance_Handler(RequestHandler):
    """BaseClass for Instance RequestHandlers
    """
    def __init__(self, application, request, **kwargs):
        RequestHandler.__init__(self, application, request, **kwargs)
        _headers = self.request.headers
        self.mn_server = _headers.get(MN_INSTANCE_SERVER,'')
        self.mn_port = _headers.get(MN_INSTANCE_PORT,'')
        self.ProductID = _headers.get(MN_PRODUCT_ID,'')
        self._instance = application._instance
        self._search_count = 2
        self._not_found_callback = self._response_no_agent

    def _request_summary(self):
        _h = self.request.headers
        res = "%s %s [%s:%s (%s)] (%s)" % \
              (self.request.method,
              self.request.uri,
              getattr(self,'mn_server','--') or '--',
              getattr(self,'mn_port','--') or '--',
              getattr(self,'ProductID','--') or '--',
              _h.get('X-Real-IP', _h.get('X-Forwarded-For', self.request.remote_ip)))
        return res

    def _response_no_agent(self):
        self.add_header(MN_RESPONSE_TYPE, MN_NO_AGENT)
        self.finish()

    def _find_desktop(self, response = None):
        _inst = self._instance
        _found_instance = None

        if response:
            if MN_INSTANCE_SERVER in response.headers and MN_INSTANCE_PORT in response.headers:
                _found_instance = (response.headers[MN_INSTANCE_SERVER], response.headers[MN_INSTANCE_PORT])
        else:
            _found_instance = _inst._getLocation(self.ProductID)

        if _found_instance:
            _inst._updateLocation(self.ProductID, _found_instance)
            self.set_header(MN_INSTANCE_SERVER, _found_instance[0])
            self.set_header(MN_INSTANCE_PORT, _found_instance[1])
            self.finish()
        else:
            self._search_count -=1
            _search = None

            if self._search_count==1:
                if _inst._instances:
                    _search={'port':_inst._instances[0]}
                else:
                    self._search_count -=1

            if self._search_count==0:
                if _inst._servers:
                    _search={'server':_inst._servers[0]}

            if _search:
                _search.update({'CustomerID':self.ProductID, 'callback':self._find_desktop})
                _inst._find(**_search)
            else:
                self._not_found_callback()

    def _range_response(self, response=None):
        _inst = self._instance
        if response:
            if not response.error and \
               MN_INSTANCE_RANGE_FROM in response.headers and MN_INSTANCE_RANGE_TO in response.headers:
                _got_range = (response.headers[MN_INSTANCE_RANGE_FROM], response.headers[MN_INSTANCE_RANGE_TO])
            else:
                _got_range = _inst._range_from_range()
                if _inst._need_range:
                    _inst._range_requests = [_inst._servers[:], _inst._instances[:]]
                    _inst._get_range(_inst.master_server, _inst.master_port,
                        _inst._response_got_range, _inst.range_size)
        else:
            _got_range = _inst._range_from_range(num=self.request.headers.get(MN_INSTANCE_RANGE_SIZE, None))

        if _got_range:
            self.set_header(MN_INSTANCE_RANGE_FROM, _got_range[0])
            self.set_header(MN_INSTANCE_RANGE_TO, _got_range[1])

        self.finish()


class inst_Hello (MN_Instance_Handler):
    """Hello handler to the whole server: processed by round-robin instance"""
    @tornado.web.asynchronous
    def post (self):
        _inst = self._instance
        _headers = {MN_INSTANCE_SERVER:self.mn_server,MN_INSTANCE_PORT:self.mn_port}
        for _port in _inst._instances:
            _inst.hello(port = _port, instance_headers=_headers)
        self.set_header(MN_KNOWN_SERVERS, ','.join(self._instance._servers))
        _inst._add_instance(self.mn_server, self.mn_port)
        self.finish()


class inst_Hello_port (MN_Instance_Handler):
    """Hello handler to the instance: port is specified"""
    @tornado.web.asynchronous
    def post (self):
        _inst = self._instance
        self.set_header(MN_KNOWN_PORTS, ','.join(self._instance._instances))
        _inst._add_instance(self.mn_server, self.mn_port)
        self.finish()


class agent_Connect (MN_Instance_Handler):
    """Desktop: connect to the chosen server: processed by round-robin instance"""
    @tornado.web.asynchronous
    def post (self):
        _inst = self._instance
        _inst._updateLocation(self.ProductID)
        self.set_header(MN_INSTANCE_PORT, _inst.port)

        for _server in _inst._servers:
            _inst.connected(self.ProductID, server = _server)
        for _port in _inst._instances:
            _inst.connected(self.ProductID, port = _port)
        self.finish()


class inst_Connected(MN_Instance_Handler):
    """Handler to inform the whole server CustomerID Desktop is connected to the specified instance:
    processed by round-robin server's instance"""
    @tornado.web.asynchronous
    def post (self):
        _inst = self._instance
        _inst._updateLocation(self.ProductID, (self.mn_server,  self.mn_port))
        #_inst._add_instance(self.mn_server,  self.mn_port)
        _headers = {MN_INSTANCE_SERVER:self.mn_server,MN_INSTANCE_PORT:self.mn_port}
        for _port in _inst._instances:
            _inst.connected(self.ProductID, port = _port, instance_headers = _headers)
        self.finish()


class inst_Connected_port(MN_Instance_Handler):
    """Handler to inform other instances CustomerID Desktop is connected to the specified one:
    instance port is specified"""
    @tornado.web.asynchronous
    def post (self):
        _inst = self._instance
        _inst._updateLocation(self.ProductID, (self.mn_server,  self.mn_port))
        #_inst._add_instance(self.mn_server,  self.mn_port)
        self.finish()


class inst_Find(MN_Instance_Handler):
    """Handler to find where the Desktop is"""
    @tornado.web.asynchronous
    def post (self):
        _desktop = self._instance._getLocation(self.ProductID)
        if _desktop:
            self.set_header(MN_INSTANCE_SERVER,_desktop[0])
            self.set_header(MN_INSTANCE_PORT,_desktop[1])
        self.finish()


class app_Client(MN_Instance_Handler):
    @tornado.web.asynchronous
    def post (self):
        self._find_desktop()


class inst_Range(MN_Instance_Handler):
    @tornado.web.asynchronous
    def post (self):
        _inst = self._instance
        if _inst.master:
            self._range_response()
        else:
            _inst._get_range(
                server= _inst.master_server,
                port = _inst.master_port,
                callback = self._range_response,
                num = _inst.range_size
            )


class inst_Range_port(MN_Instance_Handler):
    @tornado.web.asynchronous
    def post (self):
        self._range_response()


class agent_getID(MN_Instance_Handler):
    @tornado.web.asynchronous
    def post (self):
        _inst = self._instance
        _got_range=None
        if not _inst._need_range:
            _got_range = _inst._range_from_range(auto=False, num=1, fraction=0)

        if _got_range:
            self.set_header(MN_PRODUCT_ID, _got_range[0])

        if _inst._need_range:
            _inst._range_requests = [_inst._servers[:], _inst._instances[:]]
            _inst._get_range(_inst.master_server, _inst.master_port, _inst._response_got_range, _inst.range_size)

        self.finish()

