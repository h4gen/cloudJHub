import os
import sys
import socket
import binascii

# Inserts location of local code into jupyterhub at runtime.
sys.path.insert(1, '/etc/jupyterhub')

# Configuration file for Jupyter Hub

c = get_config()

c.JupyterHub.cookie_secret_file	= '/etc/jupyterhub/cookie_secret'
#c.JupyterHub.db_url		= '/etc/jupyterhub/jupyterhub.sqlite'

# To use MySQL DB
#c.JupyterHub.db_url = "mysql://{}:{}@{}/{}".format(DB_USERNAME, DB_USERPASSWORD, DB_HOSTNAME, DB_NAME)
# Replace
#   DB_NAME with the existed jupyterhub database name in MySQL server
#   DB_HOST with the DNS or the IP of the MySQL host
#   DB_USERNAME and DB_USERPASSWORD with username and password of a privileged user.
# Example :
c.JupyterHub.db_url = "postgresql://{}:{}@{}/{}".format("jupyterhubdbuser", "","","jupyterhubdb")




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

#Configure Jupyterlab (Not yet working properly)
#c.Spawner.default_url = '/lab'
c.Spawner.cmd = ['jupyterhub-singleuser']
# c.Spawner.cmd = ['jupyter-labhub']


with open("/etc/jupyterhub/api_token.txt", 'r') as f:
    api_token = f.read().strip()
c.JupyterHub.api_tokens = {api_token:"__tokengeneratoradmin"}

c.Spawner.poll_interval = 10
c.Spawner.http_timeout = 300
c.Spawner.start_timeout = 300

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
from oauthenticator.google import LocalGoogleOAuthenticator
c.JupyterHub.authenticator_class = LocalGoogleOAuthenticator
c.LocalGoogleOAuthenticator.oauth_callback_url = "https://xxx.xx/hub/oauth_callback"
c.LocalGoogleOAuthenticator.client_id = ""
c.LocalGoogleOAuthenticator.client_secret = ""
c.LocalGoogleOAuthenticator.hosted_domain = 'xxx.xx'
c.LocalGoogleOAuthenticator.login_service = '@xxx.xx'

#Map google usernames to local usernames to prevent errors frpm special characters like '.'
c.Authenticator.username_map = {}

# Development authenticator
#c.JupyterHub.authenticator_class	 = 'noauthenticator.NoAuthenticator'
c.Authenticator.add_user_cmd	 = ['adduser', '-q', '--gecos', '""', '--disabled-password', '--force-badname']
c.LocalAuthenticator.create_system_users = True

# SSL Settings. Cetificates via lets encrypt
c.JupyterHub.port = 443
c.JupyterHub.confirm_no_ssl = False
c.JupyterHub.ssl_key = '/etc/letsencrypt/live/xxx.xx/privkey.pem'
c.JupyterHub.ssl_cert = '/etc/letsencrypt/live/xxx.xx/fullchain.pem'

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
#c.Spawner.options_form =
