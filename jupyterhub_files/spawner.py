import json
import logging
import socket
import boto3
from fabric.api import env, sudo as _sudo, run as _run
from fabric.context_managers import settings
from fabric.exceptions import NetworkError
from paramiko.ssh_exception import SSHException, ChannelException
from botocore.exceptions import ClientError, WaiterError
from datetime import datetime
from tornado import gen, web
from jupyterhub.spawner import Spawner
from concurrent.futures import ThreadPoolExecutor

from models import Server
from aws_ressources import AWS_INSTANCE_TYPES

def get_local_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip_address = s.getsockname()[0]
    s.close()
    return ip_address

class ResourceNotFound(Exception):
    pass

class ServerNotFound(ResourceNotFound):
    print('Server not found in database')
    pass

class VolumeNotFound(ResourceNotFound):
    print('Volume not found in database')
    pass

with open("/etc/jupyterhub/server_config.json", "r") as f:
    SERVER_PARAMS = json.load(f) # load local server parameters

LONG_RETRY_COUNT = 120
HUB_MANAGER_IP_ADDRESS = get_local_ip_address()
NOTEBOOK_SERVER_PORT = 4444
WORKER_USERNAME  = SERVER_PARAMS["WORKER_USERNAME"]


WORKER_TAGS = [ #These tags are set on every server created by the spawner
    {"Key": "Owner", "Value": SERVER_PARAMS["WORKER_SERVER_OWNER"]},
    {"Key": "Creator", "Value": SERVER_PARAMS["WORKER_SERVER_OWNER"]},
    {"Key": "Jupyter Cluster", "Value": SERVER_PARAMS["JUPYTER_CLUSTER"]},
]

thread_pool = ThreadPoolExecutor(100)

#Logging settings
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


#Global Fabric config
class RemoteCmdExecutionError(Exception): pass
env.abort_exception = RemoteCmdExecutionError
env.abort_on_prompts = True
FABRIC_DEFAULTS = {"user":SERVER_PARAMS["WORKER_USERNAME"],
                   "key_filename":"/home/%s/.ssh/%s" % (SERVER_PARAMS["SERVER_USERNAME"], SERVER_PARAMS["KEY_NAME"])}

FABRIC_QUIET = True
#FABRIC_QUIET = False
# Make Fabric only print output of commands when logging level is greater than warning.

@gen.coroutine
def sudo(*args, **kwargs):
    ret = yield retry(_sudo, *args, **kwargs, quiet=FABRIC_QUIET)
    return ret

@gen.coroutine
def run(*args, **kwargs):
    ret = yield retry(_run, *args, **kwargs, quiet=FABRIC_QUIET)
    return ret


@gen.coroutine
def retry(function, *args, **kwargs):
    """ Retries a function up to max_retries, waiting `timeout` seconds between tries.
        This function is designed to retry both boto3 and fabric calls.  In the
        case of boto3, it is necessary because sometimes aws calls return too
        early and a resource needed by the next call is not yet available. """
    max_retries = kwargs.pop("max_retries", 10)
    timeout = kwargs.pop("timeout", 1)
    for attempt in range(max_retries):
        try:
            ret = yield thread_pool.submit(function, *args, **kwargs)
            return ret
        except (ClientError, WaiterError, NetworkError, RemoteCmdExecutionError, EOFError, SSHException, ChannelException) as e:
            #EOFError can occur in fabric
            logger.error("Failure in %s with args %s and kwargs %s" % (function.__name__, args, kwargs))
            logger.info("retrying %s, (~%s seconds elapsed)" % (function.__name__, attempt * 3))
            yield gen.sleep(timeout)
    else:
        logger.error("Failure in %s with args %s and kwargs %s" % (function.__name__, args, kwargs))
        yield gen.sleep(0.1) #this line exists to allow the logger time to print
        return ("RETRY_FAILED")

#########################################################################################################
#########################################################################################################

class InstanceSpawner(Spawner):
    """ A Spawner that starts an EC2 instance for each user.

        Warnings:
            - Because of db.commit() calls within Jupyterhub's code between yield calls in jupyterhub.user.spawn(),
            setting an attribute on self.user.server results in ORM calls and incomplete jupyterhub.sqlite Server
            entries. Be careful of setting self.user.server attributes too early in this spawner.start().

            In this spawner's start(), self.user.server.ip and self.user.server.port are set immediately before the
            return statement to alleviate the edge case where they are not always set in Jupyterhub v0.6.1. An
            improvement is made in developmental version Jupyterhub v0.7.0 where they are explicitly set.

            - It's possible for the logger to be terminated before log is printed. If your stack traces do not match up
            with your log statements, insert a brief sleep into the code where your are logging to allow time for log to
            flush.
        """

    @gen.coroutine
    def start(self):
        """ When user logs in, start their instance.
            Must return a tuple of the ip and port for the server and Jupyterhub instance. """
        self.log.debug("function start for user %s" % self.user.name)
        self.user.last_activity = datetime.utcnow()
        try:
            instance = yield self.get_instance() #cannot be a thread pool...
            #comprehensive list of states: pending, running, shutting-down, terminated, stopping, stopped.
            if instance.state["Name"] == "running":
                ec2_run_status = yield self.check_for_hanged_ec2(instance)
                if ec2_run_status == "SSH_CONNECTION_FAILED":
                    #yield self.poll()
                    #yield self.kill_instance(instance)
                    #yield retry(instance.start, max_retries=(LONG_RETRY_COUNT*2))
                    #yield retry(instance.wait_until_running, max_retries=(LONG_RETRY_COUNT*2)) #this call can occasionally fail, so we wrap it in a retry.
                    #return instance.private_ip_address, NOTEBOOK_SERVER_PORT
                    return None
                #start_worker_server will handle starting notebook
                yield self.start_worker_server(instance, new_server=False)
                self.log.debug("start ip and port: %s , %s" % (instance.private_ip_address, NOTEBOOK_SERVER_PORT))
                self.ip = self.user.server.ip = instance.private_ip_address
                self.port = self.user.server.port = NOTEBOOK_SERVER_PORT
                return instance.private_ip_address, NOTEBOOK_SERVER_PORT
            elif instance.state["Name"] in ["stopped", "stopping", "pending", "shutting-down"]:
                #For case that instance is stopped, the attributes are modified
                if instance.state["Name"] == "stopped":
                    self.log.debug(str(self.user_options['INSTANCE_TYPE']))
                    if self.user_options['INSTANCE_TYPE'] in AWS_INSTANCE_TYPES:
                        try:
                            yield retry(instance.modify_attribute, Attribute='instanceType', Value = self.user_options['INSTANCE_TYPE'])
                        except:
                            self.log.debug("Instance type for user %s could not be changed." % self.user.name)
                    else:
                        self.log.debug("Instance type for user %s could not recognized." % self.user.name)

                #Server needs to be booted, do so.
                self.log.info("Starting user %s instance " % self.user.name)
                yield retry(instance.start, max_retries=LONG_RETRY_COUNT)
                #yield retry(instance.start)
                # blocking calls should be wrapped in a Future
                yield retry(instance.wait_until_running) #this call can occasionally fail, so we wrap it in a retry.
                yield self.start_worker_server(instance, new_server=False)
                self.log.debug("%s , %s" % (instance.private_ip_address, NOTEBOOK_SERVER_PORT))
                # a longer sleep duration reduces the chance of a 503 or infinite redirect error (which a user can
                # resolve with a page refresh). 10s seems to be a good inflection point of behavior
                yield gen.sleep(10)
                self.ip = self.user.server.ip = instance.private_ip_address
                self.port = self.user.server.port = NOTEBOOK_SERVER_PORT
                return instance.private_ip_address, NOTEBOOK_SERVER_PORT
            elif instance.state["Name"] == "terminated":
                # If the server is terminated ServerNotFound is raised. This leads to the try
                self.log.debug('Instance terminated for user %s. Creating new one and try to attach old volume.' % self.user.name)
                raise ServerNotFound
            else:
                # if instance is in pending, shutting-down, or rebooting state
                raise web.HTTPError(503, "Unknown server state for %s. Please try again in a few minutes" % self.user.name)
        except (ServerNotFound, Server.DoesNotExist) as e:
            self.log.debug('Server not found raised for %s' % self.user.name)
            volume = None
            try:
                volume = yield self.get_volume() #cannot be a thread pool...
            except (VolumeNotFound, Server.DoesNotExist) as e:
                self.log.debug('Volume not found raised for %s' % self.user.name)


            self.log.info("\nCreate new server for user %s with volume %s\n" % (self.user.name, volume))
            instance = yield self.create_new_instance(Volume=volume)
            yield self.start_worker_server(instance, new_server=True)
            # self.notebook_should_be_running = False
            self.log.debug("%s , %s" % (instance.private_ip_address, NOTEBOOK_SERVER_PORT))
            # to reduce chance of 503 or infinite redirect
            yield gen.sleep(10)
            self.ip = self.user.server.ip = instance.private_ip_address
            self.port = self.user.server.port = NOTEBOOK_SERVER_PORT
            return instance.private_ip_address, NOTEBOOK_SERVER_PORT

    def clear_state(self):
        """Clear stored state about this spawner """
        super(InstanceSpawner, self).clear_state()

    @gen.coroutine
    def stop(self, now=False):
        """ When user session stops, stop user instance """
        self.log.debug("function stop")
        self.log.info("Stopping user %s instance " % self.user.name)
        try:
            instance = yield self.get_instance()
            retry(instance.stop)
            # self.notebook_should_be_running = False
        except Server.DoesNotExist:
            self.log.error("Couldn't stop server for user '%s' as it does not exist" % self.user.name)
            # self.notebook_should_be_running = False
        self.clear_state()

    @gen.coroutine
    def kill_instance(self,instance):
        self.log.debug(" Kill hanged user %s instance:  %s " % (self.user.name,instance.id))
        yield self.stop(now=True)


    # Check if the machine is hanged
    @gen.coroutine
    def check_for_hanged_ec2(self, instance):
        timerightnow    = datetime.utcnow().replace(tzinfo=None)
        ec2launchtime   = instance.launch_time.replace(tzinfo=None)
        ec2uptimeSecond = (timerightnow - ec2launchtime).seconds
        #conn_health = None
        conn_health = ""
        if ec2uptimeSecond > 180:
            # wait_until_SSHable return : 1) "some object" if SSH is established;  2) "SSH_CONNECTION_FAILED" otherwise
            conn_health  = yield self.wait_until_SSHable(instance.private_ip_address,max_retries=5)
        return(conn_health)


    @gen.coroutine
    def poll(self):
        """ Polls for whether process is running. If running, return None. If not running,
            return exit code """
        self.log.debug("function poll for user %s" % self.user.name)
        try:
            instance = yield self.get_instance()
            self.log.debug(instance.state)
            if instance.state['Name'] == 'running':
                self.log.debug("poll: server is running for user %s" % self.user.name)
                # We cannot have this be a long timeout because Jupyterhub uses poll to determine whether a user can log in.
                # If this has a long timeout, logging in without notebook running takes a long time.
                # attempts = 30 if self.notebook_should_be_running else 1
                # check if the machine is hanged
                ec2_run_status = yield self.check_for_hanged_ec2(instance)
                if ec2_run_status == "SSH_CONNECTION_FAILED":
                    #self.log.debug(ec2_run_status)
                    yield self.kill_instance(instance)
                    return "Instance Hang"
                else:
                    notebook_running = yield self.is_notebook_running(instance.private_ip_address, attempts=1)
                    if notebook_running:
                        self.log.debug("poll: notebook is running for user %s" % self.user.name)
                        return None #its up!
                    else:
                        self.log.debug("Poll, notebook is not running for user %s" % self.user.name)
                        return "server up, no instance running for user %s" % self.user.name
            else:
                self.log.debug("instance waiting for user %s" % self.user.name)
                return "instance stopping, stopped, or pending for user %s" % self.user.name
        except Server.DoesNotExist:
            self.log.error("Couldn't poll server for user '%s' as it does not exist" % self.user.name)
            # self.notebook_should_be_running = False
            return "Instance not found/tracked"

    ################################################################################################################
    ### helpers ###

    @gen.coroutine
    def is_notebook_running(self, ip_address_string, attempts=1):
        """ Checks if jupyterhub/notebook is running on the target machine, returns True if Yes, False if not.
            If an attempts count N is provided the check will be run N times or until the notebook is running, whichever
            comes first. """
        with settings(**FABRIC_DEFAULTS, host_string=ip_address_string):
            for i in range(attempts):
                self.log.debug("function check_notebook_running for user %s, attempt %s..." % (self.user.name, i+1))
                output = yield run("ps -ef | grep jupyterhub-singleuser")
                for line in output.splitlines(): #
                    #if "jupyterhub-singleuser" and NOTEBOOK_SERVER_PORT in line:
                    if "jupyterhub-singleuser" and str(NOTEBOOK_SERVER_PORT)  in line:
                        self.log.debug("the following notebook is definitely running:")
                        self.log.debug(line)
                        return True
                self.log.debug("Notebook for user %s not running..." % self.user.name)
                yield gen.sleep(1)
            self.log.error("Notebook for user %s is not running." % self.user.name)
            return False


    ###  Retun SSH_CONNECTION_FAILED if ssh connection failed
    @gen.coroutine
    def wait_until_SSHable(self, ip_address_string, max_retries=1):
        """ Run a meaningless bash command (a comment) inside a retry statement. """
        self.log.debug("function wait_until_SSHable for user %s" % self.user.name)
        with settings(**FABRIC_DEFAULTS, host_string=ip_address_string):
            ret = yield run("# waiting for ssh to be connectable for user %s..." % self.user.name, max_retries=max_retries)
        if ret == "RETRY_FAILED":
           ret = "SSH_CONNECTION_FAILED"
        return (ret)



    @gen.coroutine
    def get_instance(self):
        """ This returns a boto Instance resource; if boto can't find the instance or if no entry for instance in database,
            it raises ServerNotFound error and removes database entry if appropriate """
        self.log.debug("function get_instance for user %s" % self.user.name)
        server = Server.get_server(self.user.name)
        resource = yield retry(boto3.resource, "ec2", region_name=SERVER_PARAMS["REGION"])
        try:
            ret = yield retry(resource.Instance, server.server_id)
            self.log.debug("return for get_instance for user %s: %s" % (self.user.name, ret))
            # boto3.Instance is lazily loaded. Force with .load()
            yield retry(ret.load)
            if ret.meta.data is None:
                raise ServerNotFound
            return ret
        except ClientError as e:
            self.log.error("get_instance client error: %s" % e)
            if "InvalidInstanceID.NotFound" not in str(e):
                self.log.error("Couldn't find instance for user '%s'" % self.user.name)
                Server.remove_server(server.server_id)
                raise ServerNotFound
            raise e

    @gen.coroutine
    def get_volume(self):
        """ This returns a boto volume resource for the case no instance was found.
        If boto can't find the volume or if no entry for instance in database,
            it raises VolumeNotFound error and removes database entry if appropriate """
        self.log.debug("function get_resource for user %s" % self.user.name)
        server = Server.get_server(self.user.name)
        resource = yield retry(boto3.resource, "ec2", region_name=SERVER_PARAMS["REGION"])
        try:
            ret = yield retry(resource.Volume, server.ebs_volume_id)
            self.log.debug("return for get_volume for user %s: %s" % (self.user.name, ret))
            # boto3.Volume is lazily loaded. Force with .load()
            yield retry(ret.load)
            if ret.meta.data is None:
                Server.remove_server(server.server_id)
                self.log.info("\nVolume DNE for user %s\n" % self.user.name)
                raise VolumeNotFound
            return ret
        except ClientError as e:
            self.log.error("get_instance client error: %s" % e)
            if "InvalidInstanceID.NotFound" not in str(e):
                self.log.error("Couldn't find volume for user '%s'" % self.user.name)
                Server.remove_server(server.server_id)
                raise VolumeNotFound
            raise e



    @gen.coroutine
    def start_worker_server(self, instance, new_server=False):
        """ Runs remote commands on worker server to mount user EBS and connect to Jupyterhub. If new_server=True,
            also create filesystem on newly created user EBS"""
        self.log.debug("function start_worker_server for user %s" % self.user.name)
        # redundant variable set for get_args()
        self.ip = self.user.server.ip = instance.private_ip_address
        self.port = self.user.server.port = NOTEBOOK_SERVER_PORT
        # self.user.server.port = NOTEBOOK_SERVER_PORT
        try:
            # Wait for server to finish booting...
            wait_result = yield self.wait_until_SSHable(instance.private_ip_address,max_retries=LONG_RETRY_COUNT)
            # If first time server then setup the user name
            if new_server:
                yield self.setup_user(instance.private_ip_address)
            #start notebook
            self.log.error("\n\n\n\nabout to check if notebook is running before launching\n\n\n\n")
            notebook_running = yield self.is_notebook_running(instance.private_ip_address)
            if not notebook_running:
                yield self.remote_notebook_start(instance)
        except RemoteCmdExecutionError:
            # terminate instance and create a new one
            raise web.HTTPError(500, "Instance unreachable")

    @gen.coroutine
    def setup_user(self, privat_ip):
        """ setup_user_home  """
        if self.user.name == WORKER_USERNAME:
            pass
        else:
            if SERVER_PARAMS["USER_HOME_EBS_SIZE"] > 0:
                with settings(**FABRIC_DEFAULTS, host_string=privat_ip):
                    yield sudo("mkfs.xfs /dev/%s" %("xvdf") , user="root",  pty=False)
                    yield sudo("mkdir /jupyteruser", user="root",  pty=False)
                    yield sudo("echo /dev/%s /jupyteruser xfs defaults 1 1 >> /etc/fstab" %("xvdf") , user="root",  pty=False)
                    yield sudo("mount -a" , user="root",  pty=False)
            with settings(**FABRIC_DEFAULTS, host_string=privat_ip):
                yield sudo("mkdir -p /jupyteruser" , user="root",  pty=False)
                yield sudo("useradd -d /home/%s %s -s /bin/bash  &>/dev/null" % (self.user.name,self.user.name) , user="root",  pty=False)
                yield sudo("cp -R /home/%s /jupyteruser/%s" % (WORKER_USERNAME,self.user.name), user="root",  pty=False)
                yield sudo("ln -s /jupyteruser/%s /home/%s" % (self.user.name,self.user.name), user="root",  pty=False)
                yield sudo("chown -R %s.%s /home/%s /jupyteruser/%s" %(self.user.name,self.user.name,self.user.name,self.user.name), user="root",  pty=False)
                yield sudo("echo \" %s ALL=(ALL) NOPASSWD:ALL \" > /etc/sudoers.d/%s " % (self.user.name,self.user.name), user="root",  pty=False)
                # uncomment the line below to setup a default password for the user.
                #yield sudo('echo -e "%s\n%s" | passwd %s' % (self.user.name,self.user.name,self.user.name), pty=False)

        return True

    def user_env(self, env):
        """Augment environment of spawned process with user specific env variables."""
        import pwd
        # set HOME and SHELL for the Jupyter process
        env['HOME'] = '/home/' + self.user.name
        env['SHELL'] = '/bin/bash'
        return env


    def get_env(self):
        """Get the complete set of environment variables to be set in the spawned process."""
        env = super().get_env()
        env = self.user_env(env)
        return env


    @gen.coroutine
    def remote_notebook_start(self, instance):
        """ Do notebook start command on the remote server."""
        # Setup environments
        env = self.get_env()
        lenv=''
        for key in env:
            lenv = lenv + key + "=" + env[key] + " "
        # End setup environment
        self.log.debug("function remote_server_start %s" % self.user.name)
        worker_ip_address_string = instance.private_ip_address
        start_notebook_cmd = self.cmd + self.get_args()
        start_notebook_cmd = " ".join(start_notebook_cmd)
        self.log.info("Starting user %s jupyterhub" % self.user.name)
        with settings(user = self.user.name, key_filename = FABRIC_DEFAULTS["key_filename"],  host_string=worker_ip_address_string):
             yield sudo("%s %s --user=%s --notebook-dir=/ --allow-root > /tmp/jupyter.log 2>&1 &" % (lenv, start_notebook_cmd,self.user.name),  pty=False)

        self.log.debug("Just started the notebook for user %s with following command, waiting." % self.user.name)
        self.log.debug("%s %s --user=%s --notebook-dir=/ --allow-root > /tmp/jupyter.log 2>&1 &" % (lenv, start_notebook_cmd,self.user.name))
        try:
            self.user.settings[self.user.name] = instance.public_ip_address
        except:
            self.user.settings[self.user.name] = ""
        # self.notebook_should_be_running = True
        yield self.is_notebook_running(worker_ip_address_string, attempts=30)

    @gen.coroutine
    def create_new_instance(self, Volume=None):
        """ Creates and boots a new server to host the worker instance."""
        self.log.debug("function create_new_instance %s" % self.user.name)
        ec2 = boto3.client("ec2", region_name=SERVER_PARAMS["REGION"])
        ec2_vol = boto3.client("ec2", region_name=SERVER_PARAMS["REGION"])
        resource = boto3.resource("ec2", region_name=SERVER_PARAMS["REGION"])
        resource_vol = boto3.resource("ec2", region_name=SERVER_PARAMS["REGION"])
        BDM = []
        boot_drive = {'DeviceName': '/dev/sda1',  # this is to be the boot drive
                      'Ebs': {'VolumeSize': SERVER_PARAMS["WORKER_EBS_SIZE"],  # size in gigabytes
                              'DeleteOnTermination': True,
                              'VolumeType': 'gp2',  # This means General Purpose SSD
                              # 'Iops': 1000 }  # i/o speed for storage, default is 100, more is faster
                              }
                     }
        BDM = [boot_drive]

        # Handle EBS
        #TODO: Move to own function
        if Volume:
            volume_id = Volume.id
        elif self.user_options['EBS_VOL_ID']:
            volume_id = self.user_options['EBS_VOL_ID']
        elif self.user_options['EBS_VOL_SIZE'] > 0:
            volume = yield retry(ec2_vol.create_volume, AvailabilityZone = SERVER_PARAMS["REGION"]+'b',
                                       Size = self.user_options['EBS_VOL_SIZE'],
                                       VolumeType = 'gp2')
            volume_id = volume['VolumeId']
            yield retry(resource_vol.create_tags, Resources=[volume_id], Tags=[{"Key": "Name", "Value": 'jhub_worker_volume_' + self.user.name}])
        elif self.user_options['EBS_SNAP_ID']:
            volume = yield retry(ec2_vol.create_volume, AvailabilityZone = SERVER_PARAMS["REGION"]+'b',
                                       Size = self.user_options['EBS_VOL_SIZE'],
                                       SnapshotId=self.user_options['EBS_SNAP_ID'],
                                       VolumeType = 'gp2')
            volume_id = volume['VolumeId']
            yield retry(resource_vol.create_tags, Resources=[volume_id], Tags=[{"Key": "Name", "Value": 'jhub_worker_volume_' + self.user.name}])
        else:
            raise Exception('No EBS volume-id or volume size provided.')

        # create new instance
        reservation = yield retry(
                ec2.run_instances,
                ImageId=SERVER_PARAMS["WORKER_AMI"],
                MinCount=1,
                MaxCount=1,
                KeyName=SERVER_PARAMS["KEY_NAME"],
                InstanceType=self.user_options['INSTANCE_TYPE'],
                SubnetId=SERVER_PARAMS["SUBNET_ID"],
                SecurityGroupIds=SERVER_PARAMS["WORKER_SECURITY_GROUPS"],
                BlockDeviceMappings=BDM,
        )
        self.log.debug(reservation)
        try:
            instance_id = reservation["Instances"][0]["InstanceId"]
        except TypeError as e:
            raise Exception('AWS sends weirdly formatted JSON. Please try again...')

        instance = yield retry(resource.Instance, instance_id)
        #if an old volume is restored from a terminated instance, the user has to be updated, e.g. delted and newly saved
        if Volume:
            server = Server.get_server(self.user.name)
            Server.remove_server(server.server_id)
        Server.new_server(instance_id, self.user.name, volume_id)
        yield retry(instance.wait_until_exists)
        # add server tags; tags cannot be added until server exists
        yield retry(instance.create_tags, Tags=WORKER_TAGS)
        yield retry(instance.create_tags, Tags=[{"Key": "User", "Value": self.user.name}])
        yield retry(instance.create_tags, Tags=[{"Key": "Name", "Value": SERVER_PARAMS["WORKER_SERVER_NAME"] + '_' + self.user.name}])
        # start server
        # blocking calls should be wrapped in a Future
        yield retry(instance.wait_until_running)
        # Attach persistent EBS
        yield retry(instance.attach_volume,
                    Device='/dev/sdf',
                    VolumeId = volume_id,
                    InstanceId = instance_id)
        return instance

    def options_from_form(self, formdata):
        '''
        Parses arguments from the options form to pass to the spawner.
        Output can be accessed via self.user_options
        '''
        options = {}
        self.log.debug(str(formdata))
        inst_type = formdata['instance_type'][0].strip()
        ebs_vol_id = formdata['ebs_vol_id'][0].strip()
        ebs_vol_size = formdata['ebs_vol_size'][0].strip()
        ebs_snap_id = formdata['ebs_snap_id'][0].strip()

        options['INSTANCE_TYPE'] = inst_type if inst_type else ''
        options['EBS_VOL_ID'] = ebs_vol_id if ebs_vol_id else ''
        options['EBS_SNAP_ID'] = ebs_snap_id if ebs_snap_id else ''
        options['EBS_VOL_SIZE'] = int(ebs_vol_size) if ebs_vol_size else 0
        self.log.debug(str(options))
        return options

    def _options_form_default(self):
        '''This form is displayed when a server is started
        to give the user more control over the spwaned instance'''
        return """
        <label for="instance_type">Type in instance type</label>
        <br>
        <select name="instance_type">
        <option value='m1.small'>m1.small</option>
        <option value='m1.medium'>m1.medium</option>
        <option value='m1.large'>m1.large</option>
        <option value='m1.xlarge'>m1.xlarge</option>
        <option value='c1.medium'>c1.medium</option>
        <option value='c1.xlarge'>c1.xlarge</option>
        <option value='cc2.8xlarge'>cc2.8xlarge</option>
        <option value='m2.xlarge'>m2.xlarge</option>
        <option value='m2.2xlarge'>m2.2xlarge</option>
        <option value='m2.4xlarge'>m2.4xlarge</option>
        <option value='hs1.8xlarge'>hs1.8xlarge</option>
        <option value='t1.micro'>t1.micro</option>
        <option value='t3.nano'>t3.nano</option>
        <option value='t3.micro'>t3.micro</option>
        <option value='t3.small'>t3.small</option>
        <option value='t3.medium'>t3.medium</option>
        <option value='t3.large'>t3.large</option>
        <option value='t3.xlarge'>t3.xlarge</option>
        <option value='t3.2xlarge'>t3.2xlarge</option>
        <option value='t2.nano'>t2.nano</option>
        <option value='t2.micro'>t2.micro</option>
        <option value='t2.small'>t2.small</option>
        <option value='t2.medium'>t2.medium</option>
        <option value='t2.large'>t2.large</option>
        <option value='t2.xlarge'>t2.xlarge</option>
        <option value='t2.2xlarge'>t2.2xlarge</option>
        <option value='m5.large'>m5.large</option>
        <option value='m5.xlarge'>m5.xlarge</option>
        <option value='m5.2xlarge'>m5.2xlarge</option>
        <option value='m5.4xlarge'>m5.4xlarge</option>
        <option value='m5.12xlarge'>m5.12xlarge</option>
        <option value='m5.24xlarge'>m5.24xlarge</option>
        <option value='m4.large'>m4.large</option>
        <option value='m4.xlarge'>m4.xlarge</option>
        <option value='m4.2xlarge'>m4.2xlarge</option>
        <option value='m4.4xlarge'>m4.4xlarge</option>
        <option value='m4.10xlarge'>m4.10xlarge</option>
        <option value='m4.16xlarge'>m4.16xlarge</option>
        <option value='c5.large'>c5.large</option>
        <option value='c5.xlarge'>c5.xlarge</option>
        <option value='c5.2xlarge'>c5.2xlarge</option>
        <option value='c5.4xlarge'>c5.4xlarge</option>
        <option value='c5.9xlarge'>c5.9xlarge</option>
        <option value='c5.18xlarge'>c5.18xlarge</option>
        <option value='c4.large'>c4.large</option>
        <option value='c4.xlarge'>c4.xlarge</option>
        <option value='c4.2xlarge'>c4.2xlarge</option>
        <option value='c4.4xlarge'>c4.4xlarge</option>
        <option value='c4.8xlarge'>c4.8xlarge</option>
        <option value='r5.large'>r5.large</option>
        <option value='r5.xlarge'>r5.xlarge</option>
        <option value='r5.2xlarge'>r5.2xlarge</option>
        <option value='r5.4xlarge'>r5.4xlarge</option>
        <option value='r5.12xlarge'>r5.12xlarge</option>
        <option value='r5.24xlarge'>r5.24xlarge</option>
        <option value='r4.large'>r4.large</option>
        <option value='r4.xlarge'>r4.xlarge</option>
        <option value='r4.2xlarge'>r4.2xlarge</option>
        <option value='r4.4xlarge'>r4.4xlarge</option>
        <option value='r4.8xlarge'>r4.8xlarge</option>
        <option value='r4.16xlarge'>r4.16xlarge</option>
        <option value='p3.2xlarge'>p3.2xlarge</option>
        <option value='p3.8xlarge'>p3.8xlarge</option>
        <option value='p3.16xlarge'>p3.16xlarge</option>
        <option value='p2.xlarge'>p2.xlarge</option>
        <option value='p2.8xlarge'>p2.8xlarge</option>
        <option value='p2.16xlarge'>p2.16xlarge</option>
        <option value='g3.4xlarge'>g3.4xlarge</option>
        <option value='g3.8xlarge'>g3.8xlarge</option>
        <option value='g3.16xlarge'>g3.16xlarge</option>
        <option value='h1.2xlarge'>h1.2xlarge</option>
        <option value='h1.4xlarge'>h1.4xlarge</option>
        <option value='h1.8xlarge'>h1.8xlarge</option>
        <option value='h1.16xlarge'>h1.16xlarge</option>
        <option value='d2.xlarge'>d2.xlarge</option>
        <option value='d2.2xlarge'>d2.2xlarge</option>
        <option value='d2.4xlarge'>d2.4xlarge</option>
        <option value='d2.8xlarge'>d2.8xlarge</option>
        <option value='m3.medium'>m3.medium</option>
        <option value='m3.large'>m3.large</option>
        <option value='m3.xlarge'>m3.xlarge</option>
        <option value='m3.2xlarge'>m3.2xlarge</option>
        <option value='c3.large'>c3.large</option>
        <option value='c3.xlarge'>c3.xlarge</option>
        <option value='c3.2xlarge'>c3.2xlarge</option>
        <option value='c3.4xlarge'>c3.4xlarge</option>
        <option value='c3.8xlarge'>c3.8xlarge</option>
        <option value='g2.2xlarge'>g2.2xlarge</option>
        <option value='g2.8xlarge'>g2.8xlarge</option>
        <option value='cr1.8xlarge'>cr1.8xlarge</option>
        <option value='x1.16xlarge'>x1.16xlarge</option>
        <option value='x1.32xlarge'>x1.32xlarge</option>
        <option value='x1e.xlarge'>x1e.xlarge</option>
        <option value='x1e.2xlarge'>x1e.2xlarge</option>
        <option value='x1e.4xlarge'>x1e.4xlarge</option>
        <option value='x1e.8xlarge'>x1e.8xlarge</option>
        <option value='x1e.16xlarge'>x1e.16xlarge</option>
        <option value='x1e.32xlarge'>x1e.32xlarge</option>
        <option value='r3.large'>r3.large</option>
        <option value='r3.xlarge'>r3.xlarge</option>
        <option value='r3.2xlarge'>r3.2xlarge</option>
        <option value='r3.4xlarge'>r3.4xlarge</option>
        <option value='r3.8xlarge'>r3.8xlarge</option>
        <option value='i2.xlarge'>i2.xlarge</option>
        <option value='i2.2xlarge'>i2.2xlarge</option>
        <option value='i2.4xlarge'>i2.4xlarge</option>
        <option value='i2.8xlarge'>i2.8xlarge</option>
        <option value='m5d.large'>m5d.large</option>
        <option value='m5d.xlarge'>m5d.xlarge</option>
        <option value='m5d.2xlarge'>m5d.2xlarge</option>
        <option value='m5d.4xlarge'>m5d.4xlarge</option>
        <option value='m5d.12xlarge'>m5d.12xlarge</option>
        <option value='m5d.24xlarge'>m5d.24xlarge</option>
        <option value='c5d.large'>c5d.large</option>
        <option value='c5d.xlarge'>c5d.xlarge</option>
        <option value='c5d.2xlarge'>c5d.2xlarge</option>
        <option value='c5d.4xlarge'>c5d.4xlarge</option>
        <option value='c5d.9xlarge'>c5d.9xlarge</option>
        <option value='c5d.18xlarge'>c5d.18xlarge</option>
        <option value='r5d.large'>r5d.large</option>
        <option value='r5d.xlarge'>r5d.xlarge</option>
        <option value='r5d.2xlarge'>r5d.2xlarge</option>
        <option value='r5d.4xlarge'>r5d.4xlarge</option>
        <option value='r5d.12xlarge'>r5d.12xlarge</option>
        <option value='r5d.24xlarge'>r5d.24xlarge</option>
        <option value='z1d.large'>z1d.large</option>
        <option value='z1d.xlarge'>z1d.xlarge</option>
        <option value='z1d.2xlarge'>z1d.2xlarge</option>
        <option value='z1d.3xlarge'>z1d.3xlarge</option>
        <option value='z1d.6xlarge'>z1d.6xlarge</option>
        <option value='z1d.12xlarge'>z1d.12xlarge</option>
        <option value='f1.2xlarge'>f1.2xlarge</option>
        <option value='f1.16xlarge'>f1.16xlarge</option>
        <option value='i3.large'>i3.large</option>
        <option value='i3.xlarge'>i3.xlarge</option>
        <option value='i3.2xlarge'>i3.2xlarge</option>
        <option value='i3.4xlarge'>i3.4xlarge</option>
        <option value='i3.8xlarge'>i3.8xlarge</option>
        <option value='i3.16xlarge'>i3.16xlarge</option>
        <option value='i3.metal'>i3.metal</option>
        <option selected="selected" value="t2.nano" >t2.nano</option>
        </select>
        <br>
        <label for="ebs_vol_size">Insert EBS volume size if you start a new server and don't want to attach an old volume</label>
        <br>
        <input name="ebs_vol_size" placeholder="10"></input>
        <br>
        <label for="ebs_vol_id">Insert EBS volume id if you want to attach an old volume to a new server</label>
        <br>
        <input name="ebs_vol_id" placeholder="vol-"></input>
        <br>
        <label for="ebs_snap_id">Insert EBS snapshot id if you want to create and attach a volume from a snapshot to a new server</label>
        <br>
        <input name="ebs_snap_id" placeholder="snap-"></input>
        <br>
        """
