""" Implementation of Desktop(agent) / App(client) interaction
"""
__author__ = 'morozov'
import itertools
from datetime import timedelta
from collections import deque
from tornado.ioloop import IOLoop
import os
from tornado.options import define, options
from random import randint
from mn_stats import stats_mon
from tornado.log import access_log, app_log, gen_log

define('timeout_agent', default=60, type=int)
define('timeout_cache', default=5, type=int)
define('timeout_client', default=5, type=int)
define('timeout_no_reply', default=15, type=int)

# buffer size: use upper limit Content-Length as a key
# default 4K for any Content-Length
define('buffer_size', default={float('inf'):4096},type=dict)

MN_REQUEST_ID = 'X-IWP-RequestId'
MN_PRODUCT_ID = 'X-IWP-ProductUnivId'
MN_RESPONSE_TYPE = 'X-IWP-ResponseType'
MN_RESPONSE_CODE = 'X-iwp-responsecode'
MN_ERROR_MESSAGE = 'X-IWP-Reason'
MN_RECYCLED = 'X-IWP-IsRecycle'

MN_NO_AGENT = '0'
MN_NO_CLIENT = '0'
MN_NO_REPLY = '1'

MN_AGENT_TIMEOUT = MN_AGENT_CACHE_TIMEOUT = MN_CLIENT_TIMEOUT = MN_NO_REPLY_TIMEOUT = None

def _set_timeout_options():
    global MN_AGENT_TIMEOUT,MN_AGENT_CACHE_TIMEOUT,MN_CLIENT_TIMEOUT,MN_NO_REPLY_TIMEOUT
    MN_AGENT_TIMEOUT = timedelta(0, options.timeout_agent)
    MN_AGENT_CACHE_TIMEOUT = timedelta(0, options.timeout_cache)
    MN_CLIENT_TIMEOUT = timedelta(0, options.timeout_client)
    MN_NO_REPLY_TIMEOUT = timedelta(0, options.timeout_no_reply)
    pass

interactions = {}
awaiting = {}
awaiting_cache = {}
clients = {}
request_enum = itertools.count()
_set_timeout_options()
options.add_parse_callback(_set_timeout_options)


class Interaction:
    """Desktop/app interaction"""
    def __init__(self, client, agent):
        self.client = client
        self.agent = agent
        self.RequestID = self.agent.RequestID = self.client.RequestID = getRequestID()
        self.ProductID = client.ProductID
        self._destination = None
        self._source = None
        self._completed = False
        interactions[self.RequestID] = self
        if options.stats_enabled:
            stats_mon._stats_on('Interact',self.ProductID)

    def __call__(self, source, destination, buffer_size = None, source_finish = False):
        self._source = source
        self._stream = source.request.connection.stream
        self._content_length = int(self._source.request.headers['Content-Length'])
        self._remaining = self._content_length
        self._source_finish = source_finish

        self._buffer_size = buffer_size
        if not self._buffer_size:
            self._get_buffer_size()

        self._destination = destination

        access_log.debug('[%s]: interaction %s->%s (%i/%i)..' %
                         (self.RequestID, self._source.request.uri, self._destination.request.uri,
                          self._content_length, self._buffer_size))
        self._copy_headers()
        if not self._destination._check_closed():
            self._destination.flush(callback = self._read_chunk)

    def _read_chunk(self):
        if self._remaining < self._buffer_size:
            self._buffer_size = self._remaining
        access_log.debug('[%s]: _read_chunk %s (%i/%i)..' %
                         (self.RequestID, self._source.request.uri, self._buffer_size, self._remaining))
        self._stream.read_bytes(self._buffer_size,  self._data_callback)

    def _data_callback(self, data=None):
        self._remaining -= len(data)
        if options.stats_enabled:
            stats_mon._bytes(len(data))
        if self._remaining > 0:
            _callback = self._read_chunk
        else:
            _callback = self._destination.finish
            if self._source_finish:
                self._completed = True
                IOLoop.instance().add_callback(self._source.finish)
            else:
                self._source._timeout = IOLoop.instance().add_timeout(
                    MN_NO_REPLY_TIMEOUT, self._source._response_no_reply)
        if not self._destination._check_closed():
            access_log.debug('[%s]: _data_callback %s (%i/%i)..' %
                             (self.RequestID, self._destination.request.uri, len(data), self._remaining))
            self._destination.request.write(data, callback = _callback)

    def _copy_headers(self):
        self._destination._headers = self._source.request.headers
        if not MN_REQUEST_ID in self._destination._headers:
            self._destination.set_header(MN_REQUEST_ID, self.RequestID)
        if MN_RESPONSE_CODE in self._destination._headers:
            _status = self._destination._headers.get(MN_RESPONSE_CODE)
            try:
                _status_code=int(_status)
                self._destination.set_status(_status_code)
            except ValueError:
                raise ValueError ("Unknown status code '%s' in '%s'" % (_status, MN_RESPONSE_CODE))

    def _get_buffer_size(self):
        for (_length, _size) in options.buffer_size.items():
            if self._content_length < _length:
                self._buffer_size = _size
                break

    def _set_agent(self, agent):
        self.agent = agent
        if agent and self.client._timeout:
            IOLoop.instance().remove_timeout(self.client._timeout)
            self.client._timeout = None

    def _close_destination(self):
        if self._destination:
            self._destination.request.connection.no_keep_alive = True
            IOLoop.instance().add_callback(self._destination.finish)

    def _clear_transaction(self):
        self._destination = None
        self._source = None
        self._stream = None
        self._remaining = 0
        self._content_length = 0
        self.agent = None

    def _clear(self, resp_time=None):
        self._clear_transaction()
        self.client = None
        if options.stats_enabled:
            stats_mon._stats_off('Interact', self._completed, resp_time)


def get_Interaction(RequestID, remove = False, validateID = None):
    _interaction = None
    if RequestID in interactions:
        if remove:
            _interaction = interactions.pop(RequestID)
        else:
            _interaction = interactions[RequestID]

        if validateID and _interaction.ProductID <> validateID:
            raise ValueError, 'Not corresponding ProductID = %s' % validateID

    return _interaction

def getRequestID():
    return str(request_enum.next())

def add_registry(registry_s, request):
    ID = request.ProductID
    registry = eval(registry_s)
    if ID in registry:
        registry[ID].append(request)
    else:
        registry.update({ID: deque([request])})
    if options.stats_enabled and registry_s == 'awaiting':
        stats_mon._stats_on('Agent',ID)

def rem_registry(registry_s, request, callback = None):
    ID = request.ProductID
    registry = eval(registry_s)
    if ID in registry:
        try:
            registry[ID].remove(request)
            if not registry[ID]:
                del registry[ID]
            if options.stats_enabled and registry_s == 'awaiting':
                stats_mon._stats_off('Agent')
            if callback:
                callback(ID)
        except ValueError:
            pass

def get_registry(registry_s, ID, fifo = False, callback = None):
    registry = eval(registry_s)
    while ID in registry:
        if fifo:
            _request = registry[ID].popleft()
        else:
            _request = registry[ID].pop()
        if not registry[ID]:
            del registry[ID]
        if options.stats_enabled and registry_s == 'awaiting':
            stats_mon._stats_off('Agent')
        if not _request._closed():
            break
    else:
        return None
    if _request._timeout:
        IOLoop.instance().remove_timeout(_request._timeout)
    _request._timeout = None
    if callback:
        callback(ID)
    return _request

def add_client(client):
    add_registry('clients', client)

def add_wait(agent):
    add_registry('awaiting', agent)

def remove_wait(agent):
    rem_registry('awaiting', agent, callback = add_cache)


def remove_client(client):
    rem_registry('clients', client)

def get_Agent(ProductID):
    return get_registry('awaiting', ProductID, callback = add_cache)

def get_Client(ProductID):
    return get_registry('clients', ProductID, fifo = True, callback = add_cache)


class _cache:
    def __init__(self, ProductID):
        self.ProductID = ProductID
        self.timeout = IOLoop.instance().add_timeout(MN_AGENT_CACHE_TIMEOUT, self.clear)
        if ProductID in awaiting_cache:
            awaiting_cache [ProductID].append(self)
        else:
            awaiting_cache.update({ProductID:[self]})

    def clear(self):
        ProductID = self.ProductID
        try:
            awaiting_cache[ProductID].remove(self)
            if not awaiting_cache[ProductID]:
                del awaiting_cache[ProductID]
        except (KeyError, ValueError):
            pass


def get_cache(ProductID):
    return ProductID in awaiting_cache

def add_cache(ProductID):
    _cache(ProductID)





