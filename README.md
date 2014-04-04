MyNotes-Service
===============

*My Note Cloud Service* is a part of [My Notes](http://mynotesapp.com) solution to access your IBM Notes applications from your mobile. 
The service is used by My Notes Desktop and My Notes mobile apps connected over the internet.

Features
--------

- Cloud of servers in different parts of the world. The closest server is chosen for connection to ensure the maximum speed.
- Ability to run more then one instance on each server
- Non-blocking [Tornado](https://github.com/facebook/tornado) based HTTP Server listening of particular port as instances
- Silent switching between instances and servers
- Ability to run additional servers and instances on the fly

Third party tools
-----------------
- [Nginx](http://nginx.org/en/) as a front reverse proxy, round robin forwarding of connection requests to instances
- RRDTool-based graphing and monitoring (optional)

Installation
------------

- Install [nginx](http://nginx.org/en/)
- Install nginx [non-buffered upload patch](http://tengine.taobao.org/document/http_core.html)
- Install Python 2.7 
- Install [RRDTool](http://oss.oetiker.ch/rrdtool/) - optional RRA-stats and monitoring
- Install required python packages (you can do this just using `pip install -r requirements.txt`):
    - [Tornado](https://github.com/facebook/tornado)
	- [PyRRD](https://pypi.python.org/pypi/PyRRD) - Python bindings for RRDTool
 	- Fix PyRRD [bug](#fix-pyrrd-bug)
- Clone repository
- Configure [server](#configure-server)
- Configure [instances](#configure-instances)
- Configure [nginx site](#configure-nginx)
- Start nginx `service start nginx`
- Start [instanses](#start-instances)
- Start [monitor](#start-monitor) (optional)

Glossary
--------
- `My Notes Desktop` - Desktop application running on client computer with IBM Lotus Notes installed
- `Mobile app` - Mobile app for iPhone or Android
- `Service` - Cloud of several `servers`, Desktop is connected to the closest one.
- `Server` - Server where more than one `instance` usually running
- `Instance` - Process on a server, listening to a particular port
- `Master Instance` - Special instance in the cloud, to control distribution of CustomerID ranges between other instances.
- `Monitor` - Script running periodically to update charts and alert about instances being overloaded or down.


Configuration
-------------

Below is a sample configuration:
 - `Nginx` as a reverse proxy server
 - The only server `mynotes.your_domain.com` is in the cloud.
 - There are 2 instances `8081`, `8082` running by default.
 - `8081` is considered to a `master`
 - RRDTool-based `graphing` and `monitoring` is enabled
    	
### Configure server

Config file `mynotes.conf` contains common parameters for all instances of current server.

Timeouts and buffer-size for data transferring and logging settings:

	# --Timeout settings
	timeout_agent = 15
	timeout_cache = 10
	timeout_client = 10
	timeout_no_reply = 30

	# --buffer size: use upper bound Content-Length as a key
	buffer_size = {102400:4096, 512000:8192, 1048576:16384, float('inf'):32768}
	
	# --max clients option for AsyncHTTPClient
	http_max_clients = 60
	
	# --logging settings
	logging= 'INFO'
	stats_enabled = True
	
RRDTools settings. Defines parameters for Round-Robin-Archives to be created:

	# --RRD settings
	rrd_enabled = True
	
	#set True if rrd file should be recreated
	rrd_reset = False
	
	# stats receiving interval
	# rra settings depend on this
	# 1 minute
	stats_period = 60   
	
	# Round Robin Archives (RRA) settings
	rrd_rra = [
	(1,720),    #60->720 samples of 1 minute |(hour)--> (12 hours)
	(5,1440),   #1440 samples of 5 minutes (day)
	(60,168),   #168 samples of 1 hour (week)
	(1440,60),  #60 samples of 1 day (2 months)
	(43200,12), #12 samples of 1 month (year)
	]
Current `server` name, default `instances` and `Master`:

	# current server name
	server = 'mynotes.your_domain.com'
	
	# all your default cloud instances are listed here 
	sites = [
	('mynotes.your_domain.com','8081'),
	('mynotes.your_domain.com','8082'),
	]
	
	# master instance of your cloud
	master = ('mynotes.your_domain.com','8081')
	
Charts and alert parameters. The section is used to config `monitor`:

	# --monitor settings
	# path to graph images
	monitor_graph_path = '../imgs'

	# --chart settings - graphical representations to be created
    # graphics = {size: ([graph_periods], [graph_types])}
    # size: 'S','M','L' are acceptable values
    # graph_periods: RRDTool time offset specifications: '1h', '1day', '1m', etc.
    # graph_types: hardcoded internal graph names. None means all of existing types.
    graphics = {
    'S': [ (['6h'], ['cpu-max', 'bytes-max', 'agents_u-max', 't_completed-max', 't_unique-max', 'duration-max']),],
    'M': [
    (['1h','3h','6h','12h','1day','3day','7day','1m'],['cpu','bytes','agents_u','t_completed','t_unique','duration']),
    (['6h'], ['cpu-max', 'bytes-max', 'agents_u-max', 't_completed-max', 't_unique-max', 'duration-max']),
    ]
    }

	# --alert settings
	alert_over = 900000     #900000 mcs/sec = 90%
	alert_threshold = 0.3
	alert_grace_period = 1800
	alert_from = 'MyNotes cloud'
	alert_sender = 'mynotes@your_domain.com'
	alert_receivers = ['mynotes_admin@your_domain.com']
	alert_log = 'log/monitor.sent'
	alert_smtp = 'mail.your_domain.com'

### Configure instances

Config files for all instances are located in `./instance` directory.
Listened port is used as a file name.
Instance config file contains parameters, related  to particular instance. 
Such as log file, stats file, etc.

In our case there are 2 files: `8081.conf`, `8082.conf` in the directory.

`8081.conf`
	
	# log file name
	log_file_prefix = 'log/8081/mynotes.log'

	# stats file name
	stats_file_prefix = 'log/8081/mn_stats.log'

	# range ID file
	range_file = 'range/8081/range.ini'

	#master range ID (for master instance only!!)
	master_range = 'range/8081/master.ini'

	#optional: if Round-Robin-Achive is used for stats and monitoring
	rrd_file = 'log/8081/stats.rrd'

### Configure nginx
To configure nginx site use `mynotes.your_domain.com` file in `sites-available`. See example.

upstream config:

	# round-robin upstream
	upstream mynotes  {
    	server 127.0.0.1:8081 weight=1;
    	server 127.0.0.1:8082 weight=1;
    }
sercever context:

	server {
	    server_name yourdomain;
	    listen 80;
	....
	    client_max_body_size 32m;
	
	    # Tengine feature - http://tengine.taobao.org/document/http_core.html
	    proxy_buffering off;	
location to pass to upstream:

    # All other requests except those:
    # mache "port number" regex
    # are transfered (proxy) to MyNotes upstream (Tornado)
    location / {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://mynotes;
    }

locations to pass to particular instances:

    # Requests wich mache "port number" regex are transfered to corresponding instance of My Notes service (tornado)
    location ~ /8081$ {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8081;
    }
    location ~ /8082$ {
        proxy_set_header        Host            $host;
        proxy_set_header        X-Real-IP       $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:8082;
    }
	.....

### Fix PyRRD [bug](https://code.google.com/p/pyrrd/issues/detail?id=26):

	pyrrd.backend.external.prepareObject()

	if function == 'fetch':
    	validParams = ['resolution', 'start', 'end']
    	params = common.buildParameters(obj, validParams)
    	+ return (obj.filename, [obj.cf] + params)
    	- return (obj.filename, obj.cf, params)

Start
-----
###Start Instances
Each of the instances can be run independently from the others.
To run instances and listen particular ports:

	python mn_service.py --port=8081
	python mn_service.py --port=8082


###Start Monitor
We recommend to configure periodic start `monitor` script.
Every time `monitor` runs it updates files with stats for charts.
It also checks whether overloading or downtime is happening, and sends email alert if needed.

	python mn_monitor.py
	
Charts can be shown somewhere, e.g. special site or page can be configured.
See one of our server stats as [example](http://eu.mynotesapp.com/stats/)
