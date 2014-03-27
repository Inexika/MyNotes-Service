MyNotes-Service
===============

*My Note Cloud Service* is a part of [My Notes](http://mynotesapp.com) solution to get your Notes applications on your mobile. 
The service is intended to have My Notes Desktop and My Notes mobile app connected over the internet.

Features
--------

- Cloud of servers  in different parts of the world. The closest server is chosen for connection to ensure maximum speed.
- Ability to run more then one instance on each server
- Non-blocking [Tornado](https://github.com/facebook/tornado) based HTTPServer listening particular port as instances
- [Nginx](http://nginx.org/en/) as a front reverse proxy, round robin forwarding connect requests to instances
- Silent switching between instances and servers 
- Ability to run additional servers and instances on the fly
- RRDTool-based graphing and monitoring (optional)

Installation
------------

- Install [nginx](http://nginx.org/en/)
- Install nginx [non-buffered upload patch](http://tengine.taobao.org/document/http_core.html)
- Install Python 2.7, 
- Install [Tornado](https://github.com/facebook/tornado)
- Install dependencies:
	- Install [RRDTool](http://oss.oetiker.ch/rrdtool/) - optional to use RRA-stats and monitoring
	- Install [PyRRD](https://pypi.python.org/pypi/PyRRD) - optional to use RRA-stats and monitoring
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
- `My Notes Desktop`
- `Mobile app` - Mobile app for iPhone or Android
- `Service` - Cloud of several `servers`, Desktop is connected to the closest ones.
- `Server` - Server where more than one `instances` are usually run
- `Instance` - Process on server, listening particular port
- `Master Instance` - Special instance in the cloud, to controll CustomerID ranges distribution between other instances.
- `Monitor` - Script running periodically to update graphics and alert instances beeing overloaded or down.


Configuration
-------------

Below is a sample configuration:
 - `Nginx` as a reverse proxy server
 - The only server `your.domain.com` is in the cloud.
 - There are 2 instances `8081`, `8082` are run by default.
 - `8081` is considered to a `master`
 - RRDTool-based `graphing` and `monitoring` is enabled
    	
### Configure server

Config file `mynotes.cfg` contains common parameters for all instrances of current server.

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
	server = 'your.domain.com'
	
	# all your default cloud instances are listed here 
	sites = [
	('your.domain.com','8081'),
	('your.domain.com','8082'),
	]
	
	# master instance of your cloud
	master = ('your.domain.com','8081')
	
Graphics and alert parameters. The section is used to config `monitor`:

	# --monitor settings
	# path to graph images
	monitor_graph_path = '../imgs'
	
	# --alert settings
	alert_over = 900000     #900000 mcs/sec = 90%
	alert_threshold = 0.3
	alert_grace_period = 1800
	alert_from = 'MyNotes cloud'
	alert_sender = 'mynotes@your.domain.com'
	alert_receivers = ['mynotes_admin@your.domain.com']
	alert_log = 'log/monitor.sent'
	alert_smtp = 'domino.inexika.com'

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
To comnfigure nginx site use `your.domain.com` file in `sites-available`. See example.

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
Each of instances can be run independently on others.
To run our instances listening particular ports:

	python mn_service.py --port=8081
	python mn_service.py --port=8082


###Start Monitor
Recommended to configure periodical start `monitor` script.
Every time `monitor` runs it update files contained graphics stats.
It also checks whether overloading or down take place. Alert if needed.

	python mn_monitor.py
	
Graphics can be shown somewhere, e.g. special site or page may be configured.
See one of our server stats as [example](http://eu.mynotesapp.com/stats/)
