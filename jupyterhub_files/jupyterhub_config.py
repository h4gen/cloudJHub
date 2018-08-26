import os
import sys
import socket
import binascii

# Inserts location of local code into jupyterhub at runtime.
sys.path.insert(1, '/etc/jupyterhub')

# Configuration file for Jupyter Hub

c = get_config()

c.JupyterHub.cookie_secret_file	= '/etc/jupyterhub/cookie_secret'
c.JupyterHub.db_url		= '/etc/jupyterhub/jupyterhub.sqlite'

# To use Postgres
#c.JupyterHub.db_url = "mysql://{}:{}@{}/{}".format(DB_USERNAME, DB_USERPASSWORD, DB_HOSTNAME, DB_NAME)
# Replace
#   DB_NAME with the existed jupyterhub database name in Postgres server
#   DB_HOST with the DNS or the IP of the Postgres host
#   DB_USERNAME and DB_USERPASSWORD with username and password of a privileged user.
# Example :
#c.JupyterHub.db_url = "postgresql://{}:{}@{}/{}".format("jupyterhubdbuser", "","","jupyterhubdb")




c.JupyterHub.log_level	= "DEBUG"

#c.JupyterHub.debug_proxy = "TRUE"

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(("8.8.8.8", 80))
localip = s.getsockname()[0]

c.JupyterHub.hub_ip	= localip
c.JupyterHub.hub_port	= 8081
c.JupyterHub.port	= 80

c.ConfigurableHTTPProxy.api_url		= 'http://' + localip +':8001'
#c.ConfigurableHTTPProxy.auth_token	= 'PUT token here'
c.ConfigurableHTTPProxy.auth_token	=  binascii.b2a_hex(os.urandom(16))


#c.HubAuth.api_token		        = ' PUT token here'
c.HubAuth.api_token		        = binascii.b2a_hex(os.urandom(16))

#Configure Jupyterlab
<<<<<<< HEAD
#c.Spawner.default_url = '/lab'
c.Spawner.cmd = ['jupyterhub-singleuser']
#c.Spawner.cmd = ['jupyter-labhub']

=======
c.Spawner.default_url = '/lab'
c.Spawner.cmd = ['jupyterhub-singleuser']
>>>>>>> 4d1b05085845dbfbb347fd1df84c0dfcbd3c3013

with open("/etc/jupyterhub/api_token.txt", 'r') as f:
    api_token = f.read().strip()
c.JupyterHub.api_tokens = {api_token:"__tokengeneratoradmin"}

c.Spawner.poll_interval = 10
c.Spawner.http_timeout = 300
c.Spawner.start_timeout = 300

<<<<<<< HEAD
=======
# when there is already a spawn pending for a user
c.Spawner.options_form = """
<label for="instance_type">Type in instance type</label>
<input name="instance_type" placeholder="e.g. t2.small"></input>
"""
>>>>>>> 4d1b05085845dbfbb347fd1df84c0dfcbd3c3013
#c.JupyterHub.tornado_settings = {
#    slow_spawn_timeout : 30
#}
################ Spawner Settings ################
c.JupyterHub.spawner_class		= 'spawner.InstanceSpawner'
c.JupyterHub.last_activity_interval	= 15
c.JupyterHub.cookie_max_age_days	= 1
c.JupyterHub.admin_access		= True
c.JupyterHub.extra_log_file		= '/var/log/jupyterhub'

############# User Authenticator Settings ###############
# Production authentication option with Github. Other custom authenticators can be swapped in here.
# c.JupyterHub.authenticator_class = 'oauthenticator.LocalGitHubOAuthenticator'
# c.GitHubOAuthenticator.oauth_callback_url = "https://{URL}/hub/oauth_callback"
# c.GitHubOAuthenticator.client_id = ""
# c.GitHubOAuthenticator.client_secret = ""

# Development authenticator
c.JupyterHub.authenticator_class	 = 'noauthenticator.NoAuthenticator'
c.LocalAuthenticator.add_user_cmd	 = ['adduser', '-q', '--gecos', '""', '--disabled-password', '--force-badname']
c.LocalAuthenticator.create_system_users = True

# Add users to the admin list, the whitelist, and also record their user ids
c.Authenticator.admin_users	= admin		= set()
c.Authenticator.whitelist	= whitelist	= set()
if os.path.isfile('/etc/jupyterhub/userlist'):
    with open('/etc/jupyterhub/userlist') as f:
        for line in f:
            if line.isspace():
                continue
            parts = line.split()
            name = parts[0]
            whitelist.add(name)
            if len(parts) > 1 and parts[1] == 'admin':
                admin.add(name)



################## cull idle server ################################
cull_id = 'python3 /etc/jupyterhub/cull_idle_servers.py --url=http://' + localip + ':8081/hub/api --timeout=3600'
c.JupyterHub.services = [
    {
        'name': 'cull-idle',
        'admin': True,
        'command': cull_id.split(),
    }
]


# when there is already a spawn pending for a user
c.Spawner.options_form = """
<label for="instance_type">Type in instance type</label>
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
</select>
"""
