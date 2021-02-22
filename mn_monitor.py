""" Optional module. RRDTool-based graphing and monitoring """
__author__ = 'morozov'

try:
    from pyrrd.rrd import DataSource, RRA, RRD
    from pyrrd.graph import DEF, CDEF, VDEF, LINE, AREA, GPRINT, ColorAttributes, Graph
    from pyrrd.exceptions import ExternalCommandError
except ImportError:
    RRD=None
from tornado.options import options,define
from operator import attrgetter,mul
from itertools import imap,chain,ifilter
from math import isnan
from smtplib import SMTP, SMTPException
from datetime import datetime, timedelta
import os

RRD_files={}
RRDs={}

define('monitor_graph_path', default='./imgs')

MON_START_DAY = '1day'

# chart templates
MON_GRAPH_CPU = 'cpu'                       # CPU loading
MON_GRAPH_BYTES = 'bytes'                   # network
MON_GRAPH_DESKTOP = 'agents_u'              # Unique CustomerID Desktop connections
MON_GRAPH_TRANS = 't_completed'             # completed transactions (Mobile App -> Desktop -> Mobile App)
MON_GRAPH_TRANS_UNIQUE = 't_unique'         # Unique CustomerID transactions per min
MON_GRAPH_DURATION = 'duration'             # Average Duration of Transaction

MON_GRAPH_CPU_MAX = 'cpu-max'
MON_GRAPH_BYTES_MAX = 'bytes-max'
MON_GRAPH_DESKTOP_MAX = 'agents_u-max'
MON_GRAPH_TRANS_MAX = 't_completed-max'
MON_GRAPH_TRANS_UNIQUE_MAX = 't_unique-max'
MON_GRAPH_DURATION_MAX = 'duration-max'


MON_SIZE= {'L':(897,370),'M':(547,268),'S':(307,150)}   # chart sizes (px)
MON_DATA_STEP = 300

COLOR_SET_1 = ['#d6dbe0','#c1c9dd','#a5afd6','#7f8cbf','#5960a8','#2d338e','#0c1975']#blue
COLOR_SET_2 = ['#e2d3d6','#d8ccd1','#c6b5c4','#a893ad','#7f6689','#664975','#472b59']#purple
COLOR_SET_3 = ['#ffff00','#ff00ff','#00ff00','#00ffff',
               '#0000ff','#ff0000','#7fffd4','#00bfff',
               '#ADFF2F','#ffa07a','#00FA9A','#FF1493']#bright
COLOR_SET_3.reverse()



define('alert_over',default=900000)
define('alert_threshold', default=0.25)
define('alert_grace_period', default=1800)

define('alert_from', default='MyNotes')
define('alert_sender', default='mynotes@mynotesapp.com')
define('alert_receivers', default=['morozov@inexika.com'])
define('alert_log', default='monitor.sent')
define('alert_smtp', default='localhost')


ALERT_PERIOD = '-6m'

def color_style():
    ca = ColorAttributes()
    ca.back = '#333333'
    ca.canvas = '#333333'
    ca.shadea = '#000000'
    ca.shadeb = '#111111'
    ca.mgrid = '#CCCCCC'
    ca.axis = '#FFFFFF'
    ca.frame = '#AAAAAA'
    ca.font = '#FFFFFF'
    ca.arrow = '#FFFFFF'
    return ca

def graph_data(gtype = None, period = MON_START_DAY, size = 'M'):
    #   Makes chart one of hardcoded templates (rrdgraph)
    #   Period and size are specified as well
    if RRD is None or not options.rrd_enabled:
        return

    defs = []
    cdefs = []
    vdefs = []
    gprints =[]
    items = []
    if size=='M':
        _sizef=''
    else:
        _sizef = '-' + size.lower()

    _start = '-%s' % period
    _graph_file = '%s%s.%s.png' % (os.path.join(options.monitor_graph_path, gtype),_sizef,period)
    _label=''
    _title=''
    _step = None
    _upper_limit=None
    _low_limit=None
    _units_exponent =None
    _clrs1 = COLOR_SET_1[:]
    _clrs1.reverse()
    _clrs2 = COLOR_SET_2[:]
    _clrs2.reverse()
    _colors = _clrs1 + _clrs2

    _sorted = RRDs.keys()
    _sorted.sort()

    if gtype == MON_GRAPH_CPU:
        _colors = COLOR_SET_3[:]
        _title = 'CPU loading'
        _label = 'Percent (%)'
        _upper_limit = 100
        _low_limit = 0
        _units_exponent = 0
        for _inst in _sorted:
            defs.append(DEF(vname=('cpu_mcs_%s' % _inst),rrdfile=RRDs[_inst].filename,dsName=gtype,
                start=_start,step=_step))
            cdefs.append(CDEF(vname=('cpu_s_%s' % _inst),rpn=('cpu_mcs_%s,0.0001,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst))

    elif gtype == MON_GRAPH_BYTES:
        _colors = COLOR_SET_3[:]
        _title = 'Network'
        _label = 'bit/s'
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('bytes_sec_%s' % _inst),rrdfile=_rrd.filename,dsName=gtype,start=_start,step=_step))
            cdefs.append(CDEF(vname=('bits_%s' % _inst),rpn=('bytes_sec_%s,8,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst, stack=False))
            if size<>'S':
                vdefs.append(VDEF(vname=('traffic_%s' % _inst),
                    rpn=(','.join([('bytes_sec_%s' % _inst), 'TOTAL']))))
                gprints.append(GPRINT(vdefObj=vdefs[-1:].pop(), format=_inst +' = %.3lf %sB'))
        if len(cdefs)>1:
            cdefs.append(
                CDEF(vname='bits_total',
                    rpn=','.join(chain(imap(attrgetter('vname'),cdefs),mul(['ADDNAN'],len(cdefs)-1)))
                )
            )
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend='TOTALS', width=2))


    elif gtype == MON_GRAPH_DESKTOP:
        _title = 'Unique Desktop connections'
        _label = 'pcs/min'
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('desktop_u_%s' % _inst),rrdfile=_rrd.filename,dsName=gtype,
                start=_start,step=_step,reduce='AVERAGE'))
            cdefs.append(CDEF(vname=('desktop_u_min_%s' % _inst),rpn=('desktop_u_%s,60,*' % _inst)))
            items.append(AREA(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst, stack=True))

    elif gtype == MON_GRAPH_TRANS:
        _title = 'Completed Transactions'
        if size<>'S':
            _title += ' (app/desktop interactions)'
        _label = 'pcs/min'
        _colors = COLOR_SET_3[:]
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('completed_%s' % _inst),rrdfile=_rrd.filename,dsName=gtype,start=_start,step=_step))
            cdefs.append(CDEF(vname=('completed_min_%s' % _inst),rpn=('completed_%s,60,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst, stack=False))
            if size<>'S':
                vdefs.append(VDEF(vname=('total_completed_%s' % _inst),
                    rpn=(','.join([('completed_%s' % _inst), 'TOTAL']))))
                gprints.append(GPRINT(vdefObj=vdefs[-1:].pop(), format=_inst +' = %.0lf%s'))
        if len(cdefs)>1:
            cdefs.append(
                CDEF(vname='pcs_total',
                    rpn=','.join(chain(imap(attrgetter('vname'),cdefs),mul(['ADDNAN'],len(cdefs)-1)))
                )
            )
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend='TOTALS', width=2))

    elif gtype == MON_GRAPH_TRANS_UNIQUE:
        _title = 'Unique ClientID Transactions'
        _label = 'pcs/min'
        _colors = COLOR_SET_3[:]
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('t_unique_%s' % _inst),rrdfile=_rrd.filename,dsName=gtype,start=_start,step=_step))
            cdefs.append(CDEF(vname=('t_unique_min_%s' % _inst),rpn=('t_unique_%s,60,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst, stack=False))
        if len(cdefs)>1:
            cdefs.append(
                CDEF(vname='pcs_total',
                    rpn=','.join(chain(imap(attrgetter('vname'),cdefs),mul(['ADDNAN'],len(cdefs)-1)))
                )
            )
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend='TOTALS', width=2))

    elif gtype == MON_GRAPH_DURATION:
        _title = 'Transaction Average Duration'
        _label = 'seconds'
        _colors = COLOR_SET_3[:]
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('duration_%s' % _inst),rrdfile=_rrd.filename,dsName='duration',
                start=_start,step=_step))
            defs.append(DEF(vname=('completed_%s' % _inst),rrdfile=_rrd.filename,dsName='t_completed',
                start=_start,step=_step))
            cdefs.append(CDEF(vname=('duration_of_transaction_%s' % _inst),
                rpn=('duration_%s,0.001,*,completed_%s,0,EQ,1,completed_%s,IF,/' % (_inst,_inst,_inst))))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst))
        if len(cdefs)>1:
            cdefs.append(
                CDEF(vname='duration_avg',
                    rpn=','.join(chain(imap(attrgetter('vname'),cdefs),[str(len(cdefs)),'AVG']))
                )
            )
            items.append(LINE(defObj=cdefs[-1:].pop(), width=2, color=_colors.pop(), legend='AVERAGE'))

    elif gtype == MON_GRAPH_CPU_MAX:
        _colors = COLOR_SET_3[:]
        _title = 'CPU loading picks'
        _label = 'Percent (%)'
        _upper_limit = 100
        _low_limit = 0
        _units_exponent = 0
        for _inst in _sorted:
            defs.append(DEF(vname=('cpu_mcs_%s' % _inst),rrdfile=RRDs[_inst].filename,dsName='cpu',
                start=_start,step=_step,cdef='MAX'))
            cdefs.append(CDEF(vname=('cpu_s_%s' % _inst),rpn=('cpu_mcs_%s,0.0001,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst))

    elif gtype == MON_GRAPH_BYTES_MAX:
        _colors = COLOR_SET_3[:]
        _title = 'Network (picks)'
        _label = 'bit/s'
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('bytes_sec_%s' % _inst),rrdfile=_rrd.filename,dsName='bytes',
                cdef='MAX', start=_start,step=_step))
            cdefs.append(CDEF(vname=('bits_%s' % _inst),rpn=('bytes_sec_%s,8,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst, stack=False))
        if len(cdefs)>1:
            cdefs.append(
                CDEF(vname='bits_total',
                    rpn=','.join(chain(imap(attrgetter('vname'),cdefs),mul(['ADDNAN'],len(cdefs)-1)))
                )
            )
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend='TOTALS', width=2))


    elif gtype == MON_GRAPH_DESKTOP_MAX:
        _title = 'Unique Desktop connections (picks)'
        _label = 'pcs/min'
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('desktop_u_%s' % _inst),rrdfile=_rrd.filename,dsName='agents_u',
                cdef='MAX', start=_start,step=_step))
            cdefs.append(CDEF(vname=('desktop_u_min_%s' % _inst),rpn=('desktop_u_%s,60,*' % _inst)))
            items.append(AREA(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst, stack=True))

    elif gtype == MON_GRAPH_TRANS_MAX:
        _title = 'Completed Transactions picks\n(app/desktop interactions)'
        _label = 'pcs/min'
        _colors = COLOR_SET_3[:]
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('completed_%s' % _inst),rrdfile=_rrd.filename,dsName='t_completed',
                cdef='MAX', start=_start,step=_step))
            cdefs.append(CDEF(vname=('completed_min_%s' % _inst),rpn=('completed_%s,60,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst, stack=False))
        if len(cdefs)>1:
            cdefs.append(
                CDEF(vname='pcs_total',
                    rpn=','.join(chain(imap(attrgetter('vname'),cdefs),mul(['ADDNAN'],len(cdefs)-1)))
                )
            )
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend='TOTALS', width=2))

    elif gtype == MON_GRAPH_TRANS_UNIQUE_MAX:
        _title = 'Unique ClientID Transactions (picks)'
        _label = 'pcs/min'
        _colors = COLOR_SET_3[:]
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('t_unique_%s' % _inst),rrdfile=_rrd.filename,dsName='t_unique',
                cdef='MAX', start=_start,step=_step))
            cdefs.append(CDEF(vname=('t_unique_min_%s' % _inst),rpn=('t_unique_%s,60,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst, stack=False))
        if len(cdefs)>1:
            cdefs.append(
                CDEF(vname='pcs_total',
                    rpn=','.join(chain(imap(attrgetter('vname'),cdefs),mul(['ADDNAN'],len(cdefs)-1)))
                )
            )
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend='TOTALS', width=2))

    elif gtype == MON_GRAPH_DURATION_MAX:
        _title = 'Transaction Average Duration (picks)'
        _label = 'seconds'
        _colors = COLOR_SET_3[:]
        for (_inst,_rrd) in RRDs.items():
            defs.append(DEF(vname=('duration_avg_%s' % _inst),rrdfile=_rrd.filename,dsName='duration_avg',
                cdef='MAX', start=_start,step=_step))
            cdefs.append(CDEF(vname=('duration_avg_s_%s' % _inst),rpn=('duration_avg_%s,0.001,*' % _inst)))
            items.append(LINE(defObj=cdefs[-1:].pop(), color=_colors.pop(), legend=_inst))


    g = Graph(_graph_file, title='"%s (%s)"' % (_title,period), start=_start, vertical_label='"%s"' % _label,
        width=MON_SIZE[size][0], height=MON_SIZE[size][1], color=color_style(),
        units_exponent=_units_exponent, upper_limit=_upper_limit, lower_limit=_low_limit
    )
    g.data.extend (defs + cdefs + items + vdefs + gprints)
    try:
        g.write()
    except ExternalCommandError as e:
        pass

class alerter():
    #   Alerts if something goes wrong
    def __init__(self, RRDs=RRDs, host='localhost', server=None, last_alerts=None,):
        self.RRDs=RRDs
        self.host = host
        self.smtp = None
        self.server=server
        self.sender=options.alert_sender
        self.receivers=options.alert_receivers
        self._from=options.alert_from

        self.threshold=options.alert_threshold
        self.max_load=options.alert_over
        self.log=options.alert_log
        self.grace_period=timedelta(seconds=options.alert_grace_period)

        self._sent_alerts={}
        self.off=[]
        self.over=[]
        self.bad_rrd={}
        self.subj=[]
        self.msgs=[]
        self._fmt='%Y-%m-%d %H:%M:%S.%f'
        if last_alerts==None:
            self.last_alerts={}
            self.get_lastalerts()

    def sendmail(self):
        # Sends email alerts
        if self.subj and self.msgs:
            if not self.smtp:
                try:
                    self.smtp = SMTP(self.host)
                except SMTPException as e:
                    pass
            if self.smtp:
                for _subject, _body in zip (self.subj, self.msgs):
                    msg = '\n'.join(['From: %s <%s>',
                                     'To: MyNotes admin <%s>',
                                     'Subject: %s',
                                     '%s']) \
                          % (self._from, self.sender,'>,<'.join(self.receivers),_subject,_body)
                    try:
                        res=self.smtp.sendmail(self.sender, self.receivers, msg)
                        pass
                    except SMTPException as e:
                        pass

    def __call__(self, *args, **kwargs):
        #   Checks CPU loading and being down
        #   Prepares alert data if any
        for (_inst,_rrd) in self.RRDs.items():
            try:
                data = _rrd.fetch(cf='MAX',start=ALERT_PERIOD)[MON_GRAPH_CPU][:-1]
            except ExternalCommandError as e:
                self.bad_rrd.update({_inst:(_rrd,str(e))})
                continue

            _off = True
            _over = False
            # if any of data is OVER
            # or all of data are NaN
            for _item in data:
                if _item[1] > self.max_load:
                    _over = True
                    _off = False
                    break
                elif not isnan(_item[1]):
                    _off = False
            if _over:
                self.over.append(_inst)
            elif _off:
                self.off.append(_inst)

        _nows=datetime.now()
        if len(self.off)==len(self.RRDs) and self.check_grace_period('off'):
            # all of instances are off
            subject = 'MyNotes server "%s" seems to be down.' % self.server
            message = 'My Notes service %s seems to be down.\nAll of its instances are off.' % self.server
            self.subj.append(subject)
            self.msgs.append(message)
            self._sent_alerts.update({'off':_nows})

        elif float(len(self.over))/len(self.RRDs) >= self.threshold and self.check_grace_period('over'):
            # the whole server seems to over
            subject = '%i MyNotes instance(s) of "%s" seem(s) to be overloaded.' % (len(self.over), self.server)
            message = 'MyNotes instance(s) %s of %s server seems to be overloaded.\nCPU loading is more than %s%%.'\
                      % (','.join(self.over), self.server, str(float(self.max_load)/10000))
            self.subj.append(subject)
            self.msgs.append(message)
            self._sent_alerts.update({'over':_nows})

        elif len(self.off)+len(self.over) > 0 and \
             len(self.off)<len(self.RRDs) and \
             (len(self.over))/len(self.RRDs) < self.threshold:
            if len(self.off) and self.check_grace_period(self.off):
                # some of instances are off
                subject = '%i MyNotes instance(s) of "%s" seem(s) to be down.' % (len(self.off), self.server)
                message = 'MyNotes instance(s) %s of %s server seems to be off.' % (','.join(self.off), self.server)
                self.subj.append(subject)
                self.msgs.append(message)
                for _inst in self.off:
                    self._sent_alerts.update({_inst:_nows})

            if len(self.over) and self.check_grace_period(self.over):
                # some of instances are over
                subject = '%i MyNotes instance(s) of "%s" seem(s) to be overloaded.' % (len(self.over), self.server)
                message = 'MyNotes instance(s) %s of %s server seems to be overloaded.\nCPU loading is more than %s%%.'\
                          % (','.join(self.over), self.server, str(float(self.max_load)/10000))
                self.subj.append(subject)
                self.msgs.append(message)
                for _inst in self.over:
                    self._sent_alerts.update({_inst:_nows})

        if self.bad_rrd and self.check_grace_period('rdd'):
            subject = '%i MyNotes instance(s) RRD on "%s" cannot be read.' % (len(self.bad_rrd), self.server)
            message = '%i MyNotes instance(s) RRD on %s cannot be read.' % (len(self.bad_rrd), self.server)

            for (_inst,(_rrd,_err)) in self.bad_rrd.items():
                message += '%s: "%s" %s\n' % (_inst, _rrd.filename, _err)

            self.subj.append(subject)
            self.msgs.append(message)
            self._sent_alerts.update({'rrd':_nows})

        self.sendmail()
        self.save_lastalerts()


    def check_grace_period(self, what):
        _now=datetime.now()
        if what in ('off','over','rdd'):
            _lastdate= self.last_alerts.get(what, None)
            return not (_lastdate and _now-_lastdate < self.grace_period)
        else:
            res=False
            for _inst in list(what):
                _lastdate= self.last_alerts.get(_inst, None)
                if not (_lastdate and _now-_lastdate < self.grace_period):
                    res=True
                    break
            return res

    def get_lastalerts(self):
        if os.access(self.log, os.F_OK):
            if os.access(self.log, os.R_OK):
                fl=open(self.log,mode='r')
                fl_lines=fl.read().split('\n')
                fl.close()
                for line in fl_lines:
                    if line:
                        _rec=line.split('=')
                        try:
                            self.last_alerts.update({_rec[0]:datetime.strptime(_rec[1],self._fmt)})
                        except ValueError as e:
                            pass
            else:
                pass

    def save_lastalerts(self):
        # if os.access(self.log, os.F_OK | os.W_OK):
        # not os.access(fl, os.F_OK) and create_if_no and os.access(path, os.W_OK)
        self.last_alerts.update(self._sent_alerts)
        if self._sent_alerts.items():
            try:
                fl=open(self.log, mode='w')
                for (_key,_val) in self.last_alerts.items():
                    fl.write('='.join([_key,_val.strftime(self._fmt)])+'\n')
                fl.close()
            except IOError as e:
                pass


def _verify_graph(graph):
    #   Verifies whether graphics option values are correct
    #   Ignores incorrect parts
    graph_types_all = [MON_GRAPH_CPU,
                       MON_GRAPH_BYTES,
                       MON_GRAPH_DURATION,
                       MON_GRAPH_DESKTOP,
                       MON_GRAPH_TRANS,
                       MON_GRAPH_TRANS_UNIQUE,

                       MON_GRAPH_CPU_MAX,
                       MON_GRAPH_BYTES_MAX,
                       MON_GRAPH_DURATION_MAX,
                       MON_GRAPH_DESKTOP_MAX,
                       MON_GRAPH_TRANS_MAX,
                       MON_GRAPH_TRANS_UNIQUE_MAX,
                       ]
    if type(graph)<>dict:
        graph={}

    for key, pairs in graph.items():
        if key not in ('S','M','L') or type(pairs)<>list:
            del(graph[key])
        else:
            for _pair in pairs[:]:
                if type(_pair)<>tuple or (type(_pair)==tuple and len(_pair)<>2):
                    pairs.remove(_pair)
                else:
                    (period, gr_type)=_pair
                    if type(period)<>list or (type(gr_type)<>list and gr_type is not None):
                        pairs.remove(_pair)
                    elif gr_type is None:
                        pairs.insert(pairs.index(_pair), (period, graph_types_all))
                        pairs.remove(_pair)


define('server')
define('sites', default=[], type=list)
define('rrd_file', default=None, callback=None)
define('rrd_enabled', default=False, callback=None)
define('graphics', default={'S':(['6h'],[MON_GRAPH_CPU_MAX,
                                         MON_GRAPH_BYTES_MAX,
                                         MON_GRAPH_DURATION_MAX,
                                         MON_GRAPH_DESKTOP_MAX,
                                         MON_GRAPH_TRANS_MAX,
                                         MON_GRAPH_TRANS_UNIQUE_MAX,])}, callback=_verify_graph)

options.parse_config_file("mynotes.conf", final=False)
_server=options.server
_known_instances = options.sites
_instances = []


if _known_instances and options.server:
    for (_server, _port) in _known_instances:
        if _server == options.server and _port not in _instances:
            _instances.append(_port)

for _port in _instances:
    if not (options.rrd_enabled and RRD):
        break
    instance_conf = os.path.extsep.join(((os.path.join('instance', str(_port))),'conf'))
    if instance_conf and os.access(instance_conf, os.F_OK):
        options.rrd_file=None
        options.parse_config_file(instance_conf, final=False)
        if options.rrd_file and os.access(options.rrd_file, os.F_OK):
            RRD_files.update({_port:options.rrd_file})

for (_inst,_file) in RRD_files.items():
    RRDs.update({_inst:RRD(_file)})

for _size, pairs  in options.graphics.items():
    for (_periods, _types) in pairs:
        for _p in _periods:
            for _t in _types:
                graph_data(_t, period=_p, size=_size)

alerter(host = options.alert_smtp, server=options.server)()

