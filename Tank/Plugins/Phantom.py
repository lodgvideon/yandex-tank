from Tank import Utils
from Tank.Core import AbstractPlugin
from Tank.Plugins.Aggregator import AggregatorPlugin, AggregateResultListener
from Tank.Plugins.ConsoleOnline import ConsoleOnlinePlugin, AbstractInfoWidget
from Tank.Plugins.Stepper import Stepper
from ipaddr import AddressValueError
import ConfigParser
import datetime
import hashlib
import ipaddr
import multiprocessing
import os
import socket
import string
import subprocess
import tempfile
import time

# TODO: 1 implement phout import
# TODO: 3  chosen cases
# TODO: 2 if instances_schedule enabled - pass to phantom the top count as instances limit
# FIXME: 2 sometimes cache not used
class PhantomPlugin(AbstractPlugin):

    OPTION_TEST_DURATION = 'test_duration'
    OPTION_INSTANCES_LIMIT = 'instances'
    OPTION_AMMO_COUNT = 'ammo_count'
    OPTION_LOOP = 'loop'
    OPTION_LOOP_COUNT = 'loop_count'
    OPTION_AMMOFILE = "ammofile"
    OPTION_SCHEDULE = 'rps_schedule'
    OPTION_LOADSCHEME = 'loadscheme'
    OPTION_PORT = "port"
    OPTION_IP = 'address'
    OPTION_STPD = 'stpd_file'

    SECTION = 'phantom'
    
    def __init__(self, core):
        AbstractPlugin.__init__(self, core)
        self.process = None
        self.timeout = 1000
        self.answ_log = None
        self.phout_file = None
        self.stat_log = None
        self.phantom_log = None
        self.config = None
        self.instances = None
        self.use_caching = None
        self.http_ver = None
        self.rps_schedule = []
    
    @staticmethod
    def get_key():
        return __file__;
    
    def check_address(self):
        try:
            ipaddr.IPv6Address(self.address)
            self.ipv6 = True
        except AddressValueError:
            self.log.debug("Not ipv6 address: %s", self.address)
            self.ipv6 = False
            address_port = self.address.split(":")
            self.address = address_port[0]
            if len(address_port) > 1:
                self.port = address_port[1]
            try:
                ipaddr.IPv4Address(self.address)
            except AddressValueError:
                self.log.debug("Not ipv4 address: %s", self.address)
                ip = socket.gethostbyname(self.address)
                reverse_name = socket.gethostbyaddr(ip)[0]
                self.log.debug("Address %s ip: %s, reverse-resolve: %s", self.address, ip, reverse_name)
                if reverse_name.startswith(self.address):
                    self.address = ip
                else:
                    raise ValueError("Address %s reverse-resolved to %s, but must match", self.address, reverse_name)

    def configure(self):
        # stepper part
        self.tools_path = self.get_option("tools_path", '/usr/bin')
        self.ammo_file = self.get_option(self.OPTION_AMMOFILE, '')
        self.instances_schedule = self.get_option("instances_schedule", '')
        self.loop_limit = int(self.get_option(self.OPTION_LOOP, "-1"))
        self.ammo_limit = int(self.get_option("ammo_limit", "-1"))
        sched = self.get_option(self.OPTION_SCHEDULE, '')
        sched = " ".join(sched.split("\n"))
        sched = sched.split(')')
        self.rps_schedule = [] 
        for x in sched:
            if x.strip():
                self.rps_schedule.append(x.strip() + ')')
        self.uris = self.get_option("uris", '').split("\n")
        self.headers = self.get_option("headers", '').split("\n")
        self.http_ver = self.get_option("header_http", '1.1')
        self.autocases = self.get_option("autocases", '0')
        self.use_caching = int(self.get_option("use_caching", '1'))
        self.cache_dir = os.path.expanduser(self.get_option("cache_dir", os.getcwd()))
        self.force_stepping = int(self.get_option("force_stepping", '0'))
        
        # phantom part
        self.phantom_path = self.get_option("phantom_path", 'phantom')
        self.config = self.get_option("config", '')
        self.phantom_modules_path = self.get_option("phantom_modules_path", "/usr/lib/phantom")
        self.ssl = self.get_option("ssl", '')
        self.address = self.get_option(self.OPTION_IP, '127.0.0.1')
        self.port = self.get_option(self.OPTION_PORT, '80')
        self.tank_type = self.get_option("tank_type", 'http')
        self.answ_log = self.get_option("answ_log", tempfile.mkstemp(".log", "answ_")[1])
        self.answ_log_level = self.get_option("writelog", "none")
        if self.answ_log_level == '0':
            self.answ_log_level = 'none' 
        elif self.answ_log_level == '1':
            self.answ_log_level = 'all' 
        self.phout_file = self.get_option("phout_file", tempfile.mkstemp(".log", "phout_")[1])
        self.stat_log = self.get_option("stat_log", tempfile.mkstemp(".log", "phantom_stat_")[1])
        self.phantom_log = self.get_option("phantom_log", tempfile.mkstemp(".log", "phantom_")[1])
        self.stpd = self.get_option(self.OPTION_STPD, '')
        self.threads = self.get_option("threads", int(multiprocessing.cpu_count() / 2) + 1)
        self.instances = int(self.get_option(self.OPTION_INSTANCES_LIMIT, '1000'))
        self.gatling = ' '.join(self.get_option('gatling_ip', '').split("\n"))
        
        self.phantom_http_line = self.get_option("phantom_http_line", "")
        self.phantom_http_field_num = self.get_option("phantom_http_field_num", "")
        self.phantom_http_field = self.get_option("phantom_http_field", "")
        self.phantom_http_entity = self.get_option("phantom_http_entity", "")

        self.core.add_artifact_file(self.answ_log)        
        self.core.add_artifact_file(self.phout_file)
        self.core.add_artifact_file(self.stat_log)
        self.core.add_artifact_file(self.phantom_log)
        self.core.add_artifact_file(self.config)        

        self.check_address()
            


    def compose_config(self):
        if not self.stpd:
            raise RuntimeError("Cannot proceed with no source file")
        
        kwargs = {}
        kwargs['ssl_transport'] = "transport_t ssl_transport = transport_ssl_t { timeout = 1s } transport = ssl_transport" if self.ssl else ""
        kwargs['method_stream'] = "method_stream_ipv6_t" if self.ipv6 else "method_stream_ipv4_t"            
        kwargs['proto'] = "http_proto" if self.tank_type == 'http' else "none_proto"
        kwargs['threads'] = self.threads
        kwargs['answ_log'] = self.answ_log
        kwargs['answ_log_level'] = self.answ_log_level
        kwargs['comment_answ'] = "# " if self.answ_log_level == 'none' else ''
        kwargs['phout'] = self.phout_file
        kwargs['stpd'] = self.stpd
        if self.gatling:
            kwargs['bind'] = 'bind={ ' + self.gatling + ' }'
        else: 
            kwargs['bind'] = '' 
        kwargs['ip'] = self.address
        kwargs['port'] = self.port
        kwargs['timeout'] = self.timeout
        kwargs['instances'] = self.instances
        kwargs['stat_log'] = self.stat_log
        kwargs['phantom_log'] = self.phantom_log
        tune = ''
        if self.phantom_http_entity:
            tune += "entity = " + self.phantom_http_entity + "\n"
        if self.phantom_http_field:
            tune += "field = " + self.phantom_http_field + "\n"
        if self.phantom_http_field_num:
            tune += "field_num = " + self.phantom_http_field_num + "\n"
        if self.phantom_http_line:
            tune += "line = " + self.phantom_http_line + "\n"
        if tune:
            kwargs['reply_limits'] = 'reply_limits = {\n' + tune + "}"
        else:
            kwargs['reply_limits'] = ''

        
        handle, filename = tempfile.mkstemp(".conf", "phantom_")
        self.core.add_artifact_file(filename)
        self.log.debug("Generating phantom config: %s", filename)
        template_str = open(os.path.dirname(__file__) + "/phantom.conf.tpl", 'r').read()
        tpl = string.Template(template_str)
        config = tpl.substitute(kwargs)

        os.write(handle, config)
        return filename
        

    def prepare_stepper(self):
        self.stpd = self.get_stpd_filename()
        self.core.set_option(self.SECTION, self.OPTION_STPD, self.stpd)
        if self.use_caching and not self.force_stepping and os.path.exists(self.stpd) and os.path.exists(self.stpd + ".conf"):
            self.log.info("Using cached stpd-file: %s", self.stpd)
            stepper = Stepper(self.stpd) # just to store cached data
            self.read_cached_options(self.stpd + ".conf", stepper)
        else:
            stepper = self.make_stpd_file(self.stpd)
        
        self.core.set_option(AggregatorPlugin.SECTION, AggregatorPlugin.OPTION_CASES, stepper.cases)
        self.core.set_option(AggregatorPlugin.SECTION, AggregatorPlugin.OPTION_STEPS, stepper.steps)
        self.core.set_option(self.SECTION, self.OPTION_LOADSCHEME, stepper.loadscheme)
        self.core.set_option(self.SECTION, self.OPTION_LOOP_COUNT, str(stepper.loop_count))
        self.core.set_option(self.SECTION, self.OPTION_AMMO_COUNT, str(stepper.ammo_count))
        self.calculate_test_duration(stepper.steps)
                
        self.core.config.flush(self.stpd + ".conf")
        

    def prepare_test(self):
        self.prepare_stepper()     
                
        aggregator = None
        try:
            aggregator = self.core.get_plugin_of_type(AggregatorPlugin)
        except Exception, ex:
            self.log.warning("No aggregator found: %s", ex)

        if aggregator:
            aggregator.set_source_files(self.phout_file, self.stat_log)
            self.timeout = aggregator.get_timeout()

        if not self.config:
            self.config = self.compose_config()
        args = [self.phantom_path, 'check', self.config]
        
        rc = Utils.execute(args, catch_out=True)
        if rc:
            raise RuntimeError("Subprocess returned %s",)    

        try:
            console = self.core.get_plugin_of_type(ConsoleOnlinePlugin)
        except Exception, ex:
            self.log.debug("Console not found: %s", ex)
            console = None
            
        if console:    
            widget = PhantomProgressBarWidget(self)
            console.add_info_widget(widget)
            aggregator = self.core.get_plugin_of_type(AggregatorPlugin)
            aggregator.add_result_listener(widget)

            widget = PhantomInfoWidget(self)
            console.add_info_widget(widget)
            aggregator = self.core.get_plugin_of_type(AggregatorPlugin)
            aggregator.add_result_listener(widget)

        
    def start_test(self):
        args = [self.phantom_path, 'run', self.config]
        self.log.debug("Starting %s with arguments: %s", self.phantom_path, args)
        self.phantom_start_time = time.time()
        self.process = subprocess.Popen(args, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    

    def is_test_finished(self):
        Utils.log_stdout_stderr(self.log, self.process.stdout, self.process.stderr, self.SECTION)

        rc = self.process.poll()
        if rc != None:
            self.log.info("Phantom done its work with exit code: %s", rc)
            return rc
        else:
            return -1

    
    def end_test(self, retcode):
        if self.process and self.process.poll() == None:
            self.log.warn("Terminating phantom process with PID %s", self.process.pid)
            self.process.terminate()
        else:
            self.log.debug("Seems phantom finished OK")
        return retcode
            
            
    def get_stpd_filename(self):
        if self.use_caching:
            sep = "|"
            hasher = hashlib.md5()
            hashed_str = os.path.realpath(self.ammo_file) + sep + self.instances_schedule + sep + str(self.loop_limit)
            hashed_str += sep + str(self.ammo_limit) + sep + ';'.join(self.rps_schedule) + sep + self.autocases
            hashed_str += sep + ";".join(self.uris) + sep + ";".join(self.headers)
            
            if not self.ammo_file:
                raise RuntimeError("Ammo file not specified")

            if not os.path.exists(self.ammo_file):
                raise RuntimeError("Ammo file not found: %s", self.ammo_file)
            
            for stat_option in os.stat(self.ammo_file):
                hashed_str += ";" + str(stat_option)
            self.log.debug("stpd-hash source: %s", hashed_str)
            hasher.update(hashed_str)            
            
            if not os.path.exists(self.cache_dir):
                os.makedirs(self.cache_dir)
            stpd = self.cache_dir + '/' + os.path.basename(self.ammo_file) + "_" + hasher.hexdigest() + ".stpd"
            self.log.debug("Generated cache file name: %s", stpd)
        else:
            stpd = os.path.realpath("ammo.stpd")
    
        return stpd
    


    def calculate_test_duration(self, steps):
        # calc total test duration
        steps = steps.split(' ')
        duration = 0
        for step in steps:
            if step.strip():
                duration += int(step[1:-1].split(';')[1])
        
        self.core.set_option(self.SECTION, self.OPTION_TEST_DURATION, str(duration))

    def read_cached_options(self, cached_config, stepper):
        self.log.debug("Reading cached stepper options: %s", cached_config)
        external_stepper_conf = ConfigParser.ConfigParser()
        external_stepper_conf.read(cached_config)
        stepper.cases = external_stepper_conf.get(AggregatorPlugin.SECTION, AggregatorPlugin.OPTION_CASES)
        stepper.steps = external_stepper_conf.get(AggregatorPlugin.SECTION, AggregatorPlugin.OPTION_STEPS)
        stepper.loadscheme = external_stepper_conf.get(self.SECTION, self.OPTION_LOADSCHEME)
        stepper.loop_count = external_stepper_conf.get(self.SECTION, self.OPTION_LOOP_COUNT)
        stepper.ammo_count = external_stepper_conf.get(self.SECTION, self.OPTION_AMMO_COUNT)


    def make_stpd_file(self, stpd):
        self.log.info("Making stpd-file: %s", self.stpd)
        stepper = Stepper(stpd)
        stepper.autocases = int(self.autocases)
        stepper.rps_schedule = self.rps_schedule
        stepper.instances_schedule = self.instances_schedule
        stepper.loop_limit = self.loop_limit
        stepper.uris = self.uris
        stepper.headers = self.headers
        stepper.header_http = self.http_ver
        stepper.ammofile = self.ammo_file

        stepper.generate_stpd()
        return stepper
        

class PhantomProgressBarWidget(AbstractInfoWidget, AggregateResultListener):
    def get_index(self):
        return 0

    def __init__(self, sender):
        AbstractInfoWidget.__init__(self)
        self.owner = sender 
        self.ammo_progress = 0
        self.ammo_count = int(self.owner.core.get_option(self.owner.SECTION, self.owner.OPTION_AMMO_COUNT))
        self.test_duration = int(self.owner.core.get_option(self.owner.SECTION, self.owner.OPTION_TEST_DURATION))

    def render(self, screen):
        res = ""
        res += self.get_progressbar(screen.right_panel_width, screen.markup)
        res += "\n"
        dur_seconds = int(time.time()) - int(self.owner.phantom_start_time)
        duration = datetime.timedelta(seconds=dur_seconds)
        dur = 'Duration: %s' % str(duration)

        eta_time = 'N/A' 
        
        if self.test_duration and self.test_duration >= dur_seconds:
            eta_time = datetime.timedelta(seconds=self.test_duration - dur_seconds)
        elif self.ammo_progress:
            left_part = self.ammo_count - self.ammo_progress
            secs = int(float(dur_seconds) / float(self.ammo_progress) * float(left_part))
            eta_time = datetime.timedelta(seconds=secs)
        eta = 'ETA: %s' % eta_time
        spaces = ' ' * (screen.right_panel_width - len(eta) - len(dur) - 1)
        res += dur + ' ' + spaces + eta

        return res

    # TODO: 1 change PB to use time when it is present, switch to ammo count otherwise
    def get_progressbar(self, width, markup):
        progress = float(self.ammo_progress) / float(self.ammo_count)
        perc = float(int(1000 * progress)) / 10
        str_perc = str(perc) + "%"
        self.log.debug("PB: count %s progr %s perc %s", self.ammo_count, self.ammo_progress, perc)
        
        pb_width = width - 1 - len(str_perc)
        
        res = markup.BG_GREEN + ' ' * int(pb_width * progress) + markup.RESET + markup.GREEN + '-' * (pb_width - int(pb_width * progress)) + markup.RESET + ' '
        res += str_perc
        return res

    def aggregate_second(self, second_aggregate_data):
        self.ammo_progress += second_aggregate_data.overall.RPS


# TODO: 3 widget data: loadscheme?    
# TODO: 1 req/answ sizes in widget - last sec and curRPS
class PhantomInfoWidget(AbstractInfoWidget, AggregateResultListener):

    def get_index(self):
        return 2

    def __init__(self, sender):
        AbstractInfoWidget.__init__(self)
        self.owner = sender 
        self.instances = 0
        self.planned = 0
        self.RPS = 0    
        self.instances_limit = int(self.owner.core.get_option(PhantomPlugin.SECTION, PhantomPlugin.OPTION_INSTANCES_LIMIT))
        self.selfload = 0
        self.time_lag = 0
        self.ammo_count = int(self.owner.core.get_option(self.owner.SECTION, self.owner.OPTION_AMMO_COUNT))
        self.planned_rps_duration = 0

    def render(self, screen):
        template = "Hosts: %s => %s:%s\n Ammo: %s\nCount: %s"
        data = (socket.gethostname(), self.owner.address, self.owner.port, os.path.basename(self.owner.ammo_file), self.ammo_count)
        res = template % data
        
        res += "\n\n"
        
        res += "Active instances: "
        if float(self.instances) / self.instances_limit > 0.8:
            res += screen.markup.RED + str(self.instances) + screen.markup.RESET
        elif float(self.instances) / self.instances_limit > 0.5:
            res += screen.markup.YELLOW + str(self.instances) + screen.markup.RESET
        else:
            res += str(self.instances)
        
        res += "\nPlanned requests: %s    %s\nActual responses: " % (self.planned, datetime.timedelta(seconds=self.planned_rps_duration))
        if not self.planned == self.RPS:
            res += screen.markup.YELLOW + str(self.RPS) + screen.markup.RESET
        else:
            res += str(self.RPS)
                
        res += "\n       Self load: "
        if self.selfload > 30:
            res += screen.markup.RED + str(self.selfload) + screen.markup.RESET
        elif self.selfload > 10:
            res += screen.markup.YELLOW + str(self.selfload) + screen.markup.RESET
        else:
            res += str(self.selfload)

        res += "%\n        Time lag: "        
        if self.time_lag > 15:
            res += screen.markup.RED + str(datetime.timedelta(seconds=self.time_lag)) + screen.markup.RESET
        elif self.time_lag > 3:
            res += screen.markup.YELLOW + str(datetime.timedelta(seconds=self.time_lag)) + screen.markup.RESET
        else:
            res += str(datetime.timedelta(seconds=self.time_lag))
                
        return res

    def aggregate_second(self, second_aggregate_data):
        self.instances = second_aggregate_data.overall.active_threads
        if self.planned == second_aggregate_data.overall.planned_requests:
            self.planned_rps_duration += 1
        else:
            self.planned = second_aggregate_data.overall.planned_requests
            self.planned_rps_duration = 1
        
        self.RPS = second_aggregate_data.overall.RPS
        self.selfload = second_aggregate_data.overall.selfload
        self.log.debug("%s %s", second_aggregate_data.time.timetuple(), self.owner.phantom_start_time)
        self.time_lag = int((datetime.datetime.now() - second_aggregate_data.time).total_seconds())
    
    