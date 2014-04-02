"""Optional module to collect stats, including RRD-data if it's enabled"""
__author__ = 'morozov'
from tornado.options import define, options
from tornado.ioloop import IOLoop, PeriodicCallback
import logging
import logging.handlers
from tornado.log import gen_log
import psutil
from os import getpid,access,F_OK
from datetime import date
from time import time
from itertools import imap
try:
    from pyrrd.rrd import DataSource, RRA, RRD
    from pyrrd.exceptions import ExternalCommandError
except ImportError:
    RRD = None
try:
    import resource
except ImportError:
    resource = None



define('stats_enabled', default=False)
define('stats_file_prefix', default='stats.log', type=str)
define('stats_period', default=60, type=int)

define('rrd_file', default='stats.rdd', type=str)
define('rrd_enabled', default=False)
define('rrd_rra', default=[(1,60),(5,72),(6,24)])
define('rrd_reset', default=False)

stats={}

class PeriodicCallback_start(PeriodicCallback):
    def start(self, start_timeout=0):
        """Starts the timer on start_timeout: number_of_secs after new minute starts"""
        self._running = True
        if start_timeout == None:
            self._next_timeout = self.io_loop.time()
        else:
            current_time = self.io_loop.time()
            self._next_timeout = current_time + (60 - current_time % 60) + start_timeout
            while self._next_timeout <= self.io_loop.time():
                self._next_timeout += 60
        self._schedule_next()


class stats_monitor:
    def __init__(self, period = 0, enabled = False):
        self.period = period
        self.enabled = enabled
        self.logger = logging.getLogger('workplace_stats')
        self.rrd = None
        self.init_stats()

        if period:
            self.callback = PeriodicCallback_start(self._run, self.period*1000)
            if enabled:
                self.callback.start()
        else:
            self.callback = None

    def init_stats(self):
        stats ['Interact_On']=0
        stats ['Interact_Off']=0
        stats ['Interact_Start']=0
        stats ['Interact_Period']=0
        stats ['Interact_Current']=0
        stats ['Interact_Min']=0
        stats ['Interact_Max']=0
        stats ['Interact_Unique']=0
        stats ['Interact_Failed']=0
        stats ['Interact_Unique_ID']={}
        stats ['Interact_AvgTime']=[0.0,0]

        stats ['Agent_On']=0
        stats ['Agent_Off']=0
        stats ['Agent_Start']=0
        stats ['Agent_Period']=0
        stats ['Agent_Current']=0
        stats ['Agent_Unique']=0
        stats ['Agent_Unique_ID']={}

        stats ['Max_RSS']=0
        stats ['CPU_User']=0
        stats ['CPU_System']=0

        stats ['CPU_percent']=0
        stats ['CPU_time']=0
        #stats ['CPU_delta']=0

        stats ['Bytes_Total'] = 0
        stats ['Bytes_Period'] = 0


    def init_rdd(self):
        filename = options.rrd_file
        if not options.rrd_reset and access(filename, F_OK):
            myRRD = RRD(filename)
        else:
            heartbeat=options.stats_period*2
            dataSources = [
                DataSource(dsName='agents_u', dsType='ABSOLUTE', heartbeat=heartbeat),
                DataSource(dsName='t_unique', dsType='ABSOLUTE', heartbeat=heartbeat),
                DataSource(dsName='t_started', dsType='ABSOLUTE', heartbeat=heartbeat),
                DataSource(dsName='t_completed', dsType='ABSOLUTE', heartbeat=heartbeat),
                DataSource(dsName='t_failed', dsType='ABSOLUTE', heartbeat=heartbeat),
                DataSource(dsName='bytes', dsType='ABSOLUTE', heartbeat=heartbeat),
                DataSource(dsName='cpu', dsType='DERIVE', heartbeat=heartbeat,minval=0),
                DataSource(dsName='duration', dsType='ABSOLUTE', heartbeat=heartbeat),
                DataSource(dsName='duration_avg', dsType='GAUGE', heartbeat=heartbeat),
            ]
            roundRobinArchives = []
            for (_steps, _rows) in options.rrd_rra:
                roundRobinArchives.append(RRA(cf='AVERAGE', xff=0.5, steps=_steps, rows=_rows))
                roundRobinArchives.append(RRA(cf='MAX', xff=0.5, steps=_steps, rows=_rows))
            myRRD = RRD(filename, ds=dataSources, rra=roundRobinArchives, step=options.stats_period)
            myRRD.create(debug=True)
        return myRRD

    def init_log(self):
        self.logger.setLevel(logging.INFO)
        if options.stats_file_prefix and self.enabled:
            channel = logging.handlers.RotatingFileHandler(
                filename=options.stats_file_prefix,
                maxBytes=options.log_file_max_size,
                backupCount=options.log_file_num_backups)
            channel.setFormatter(logging.Formatter('%(asctime)s, '+ options.port + ', %(message)s'))
            self.logger.addHandler(channel)

    def reset(self):
        self.period = options.stats_period
        self.enabled = options.stats_enabled
        self._stop()
        self.init_log()
        if options.rrd_enabled and RRD:
            self.rrd = self.init_rdd()
        self.callback = PeriodicCallback_start(self._run, self.period * 1000)
        self._start()

    def _start(self):
        if self.callback and self.enabled:
            self.callback.start()

    def _stop(self):
        if self.callback:
            self.callback.stop()

    def _stats_on(self, key, ID):
        if not self.enabled or not key in ['Interact','Agent']:
            return
        stats [key+'_On']+=1
        stats [key+'_Current']+=1
        if ID not in stats[key+'_Unique_ID']:
            stats[key+'_Unique_ID'].update({ID:1})
            stats [key+'_Unique']+=1
        if key+'_Max' in stats:
            if stats[key+'_Max'] < stats[key+'_Current']:
                stats[key+'_Max'] = stats[key+'_Current']
            if stats[key+'_Min'] > stats[key+'_Current']:
                stats[key+'_Min'] = stats[key+'_Current']

    def _stats_off(self, key, completed=True, resp_time=None):
        if not self.enabled or not key in ['Interact','Agent']:
            return
        stats [key+'_Off']+=1
        stats [key+'_Current']-=1
        if not completed:
            stats [key+'_Failed']+=1
        elif resp_time:
            stats [key+'_AvgTime'][1]+=1
            stats [key+'_AvgTime'][0]+=resp_time
        if key+'_Max' in stats:
            if stats[key+'_Max'] < stats[key+'_Current']:
                stats[key+'_Max'] = stats[key+'_Current']
            if stats[key+'_Min'] > stats[key+'_Current']:
                stats[key+'_Min'] = stats[key+'_Current']

    def _bytes(self, num):
        stats ['Bytes_Total']+=num
        stats ['Bytes_Period']+=num

    def _run(self):
        for key in ['Agent','Interact']:
            stats [key+'_Period'] = stats [key+'_Start'] + stats [key+'_On']
        if resource:
            stats['Max_RSS'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            stats['CPU_User'] = resource.getrusage(resource.RUSAGE_SELF).ru_utime - stats['CPU_User']
            stats['CPU_System'] = resource.getrusage(resource.RUSAGE_SELF).ru_stime - stats['CPU_System']

        p=psutil.Process(getpid())

        CPU_time = p.get_cpu_times()
        #stats['CPU_delta'] = CPU_time[0]+CPU_time[1] - stats['CPU_time']
        stats['CPU_percent'] = (CPU_time[0]+CPU_time[1] - stats['CPU_time'])/self.period
        stats['CPU_time'] = CPU_time[0]+CPU_time[1]

        fmt = '{:d}, {:.3f}, {:.3f}, {:.3%}, {:.3f}, {:d}, {:d}, {:d}, {:d}, {:d}, {:d}, {:d}, {:.3f}, {:d}, {:d}'
        self.logger.info(fmt.format(
            stats['Max_RSS'],    #0
            stats['CPU_User'],   #1
            stats['CPU_System'], #2
            stats['CPU_percent'],    #3
            stats['CPU_time'],       #4
            stats['Agent_Period'],   #5
            stats['Agent_Unique'],   #6
            stats['Interact_Period'],#7
            stats['Interact_Unique'],#8
            stats['Interact_Min'],   #9
            stats['Interact_Max'],   #10
            stats['Interact_Failed'],#11
            stats['Interact_AvgTime'][0]/stats['Interact_AvgTime'][1] if stats['Interact_AvgTime'][1] else 0,#12
            stats['Bytes_Total'],#13
            stats['Bytes_Period'],#14
            )
        )

        #todo: run_rrd
        self._run_rrd()

        stats['Bytes_Period']=0
        for key in ['Agent','Interact']:
            stats[key+'_Start'] = stats[key+'_Min'] = stats[key+'_Max'] = stats[key+'_Current']
            stats[key+'_Off'] = stats[key+'_On'] = stats[key+'_Unique'] = 0
            stats [key+'_Unique_ID']={}
            if key == 'Interact':
                stats[key+'_Failed']=0
                stats[key+'_AvgTime']=[0.0,0]

    def _run_rrd(self):
        if self.rrd:
            _time = int(time())
            _data = [stats['Agent_Unique'],
                     stats['Interact_Unique'],
                     stats['Interact_On'],          #started
                     stats['Interact_AvgTime'][1],  #completed
                     stats['Interact_Failed'],      #failed
                     stats['Bytes_Period'],
                     int(stats['CPU_time']*pow(10,6)),  #mcs
                     stats['Interact_AvgTime'][0],      #duration
                     stats['Interact_AvgTime'][0]/stats['Interact_AvgTime'][1] if stats['Interact_AvgTime'][1] else 0,
                    ]

            _values = tuple(imap(str,_data))
            self.rrd.bufferValue(_time, *_values)
            try:
                self.rrd.update(debug=True)
            except ExternalCommandError as e:
                gen_log.error(str(e))
                self.rrd.values=[]


stats_mon = stats_monitor()
options.add_parse_callback(stats_mon.reset)


