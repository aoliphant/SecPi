#!/usr/bin/env python

import sys

if(len(sys.argv)>1):
	PROJECT_PATH = sys.argv[1]
	sys.path.append(PROJECT_PATH)
else:
	print("Error initializing Webinterface, no path given!");
	sys.exit(1)

import os
import json
import traceback
import logging
import logging.config
import subprocess


# web framework
import cherrypy
from cherrypy.lib import auth_digest
from cherrypy import _cperror

# db connection
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column
from sqlalchemy.types import String, Integer

# web <--> db
from cp_sqlalchemy import SQLAlchemyTool, SQLAlchemyPlugin

# templating engine
from mako.template import Template
from mako.lookup import TemplateLookup

# rabbitmq
import pika

# our stuff
from tools.db import objects
from tools import config
from tools import utils

from mako_template_tool import MakoTemplateTool

# sub pages
from sites.sensors import SensorsPage
from sites.zones import ZonesPage
from sites.setups import SetupsPage
from sites.alarms import AlarmsPage
from sites.workers import WorkersPage
from sites.actions import ActionsPage
from sites.notifiers import NotifiersPage
from sites.params import ParamsPage
from sites.logs import LogEntriesPage
from sites.setupszones import SetupsZonesPage
from sites.workersactions import WorkersActionsPage

config.load(PROJECT_PATH +"/webinterface/config.json")

class Root(object):

	def __init__(self):
		cherrypy.config.update({'request.error_response': self.handle_error})
		cherrypy.config.update({'error_page.404': self.error_404})
		cherrypy.config.update({'error_page.401': self.error_401})
		
		self.sensors = SensorsPage()
		self.zones = ZonesPage()
		self.setups = SetupsPage()
		self.alarms = AlarmsPage()
		self.workers = WorkersPage()
		self.actions = ActionsPage()
		self.notifiers = NotifiersPage()
		self.params = ParamsPage()
		self.logs = LogEntriesPage()
		self.setupszones = SetupsZonesPage();
		self.workersactions = WorkersActionsPage();
		
		try:
			credentials = pika.PlainCredentials(config.get('rabbitmq')['user'], config.get('rabbitmq')['password'])
			parameters = pika.ConnectionParameters(credentials=credentials,
				host=config.get('rabbitmq')['master_ip'],
				port=5671,
				ssl=True,
				socket_timeout=10,
				ssl_options = {
					"ca_certs":PROJECT_PATH+"/certs/"+config.get('rabbitmq')['cacert'],
					"certfile":PROJECT_PATH+"/certs/"+config.get('rabbitmq')['certfile'],
					"keyfile":PROJECT_PATH+"/certs/"+config.get('rabbitmq')['keyfile']
				}
			)
			connection = pika.BlockingConnection(parameters=parameters)
			self.channel = connection.channel()
			self.channel.exchange_declare(exchange=utils.EXCHANGE, exchange_type='direct')

			self.channel.queue_declare(queue=utils.QUEUE_ON_OFF)
			self.channel.queue_bind(exchange=utils.EXCHANGE, queue=utils.QUEUE_ON_OFF)
		except Exception as e:
			cherrypy.log("Error connecting to Queue! %s"%e)
	
	@property
	def db(self):
		return cherrypy.request.db
		
	@property
	def lookup(self):
		return cherrypy.request.lookup
	
	def handle_error(self):
		if('Content-Type' in cherrypy.request.headers and 'application/json' in cherrypy.request.headers['Content-Type'].lower()):
			exc_type, exc_value, exc_traceback = sys.exc_info()
			cherrypy.response.status = 200
			cherrypy.response.body = json.dumps({'status':'error', 'message': "An exception occured during processing! %s"%exc_value, 'traceback':traceback.format_exc() })
		else:
			tmpl = self.lookup.get_template("500.mako")
			cherrypy.response.status = 500
			cherrypy.response.body = tmpl.render(page_title="Error!", traceback=traceback.format_exc())

	def error_404(self, status, message, traceback, version):
		tmpl = self.lookup.get_template("404.mako")
		cherrypy.response.status = 404
		return tmpl.render(page_title="File not found!")

	def error_401(self, status, message, traceback, version):
		tmpl = self.lookup.get_template("401.mako")
		cherrypy.response.status = 401
		return tmpl.render(page_title="Not Authorized!")

	@cherrypy.expose
	def index(self):
		tmpl = self.lookup.get_template("index.mako")
		return tmpl.render(page_title="Welcome")
	
	@cherrypy.expose
	def test(self):
		tmpl = self.lookup.get_template("test.mako")
		return tmpl.render(page_title="Testing")
	
	@cherrypy.expose
	def change_credentials(self):
		tmpl = self.lookup.get_template("change_credentials.mako")
		return tmpl.render(page_title="Change Login Credentials")

	@cherrypy.expose
	@cherrypy.tools.json_in()
	@cherrypy.tools.json_out(handler=utils.json_handler)
	def activate(self):
		if(hasattr(cherrypy.request, 'json')):
			id = cherrypy.request.json['id']
			
			if(id and id > 0):
				su = self.db.query(objects.Setup).get(int(id))
				try:
					if(hasattr(self, "channel")):
						su.active_state = True
						self.db.commit()
						ooff = { 'active_state': True }
						self.channel.basic_publish(exchange=utils.EXCHANGE, routing_key=utils.QUEUE_ON_OFF, body=json.dumps(ooff))
					else:
						return {'status':'error', 'message': "Error activating %s! No connection to queue server!"%su.name }
						
				except Exception as e:
					su.active_state = False;
					self.db.commit()
					return {'status':'error', 'message': "Error activating! %s"%e }
				else:
					return {'status': 'success', 'message': "Activated setup %s!"%su.name}
				
			return {'status':'error', 'message': "Invalid ID!" }
		
		return {'status': 'error', 'message': 'No data recieved!'}

	@cherrypy.expose
	@cherrypy.tools.json_in()
	@cherrypy.tools.json_out(handler=utils.json_handler)
	def deactivate(self):
		if(hasattr(cherrypy.request, 'json')):
			id = cherrypy.request.json['id']
			
			if(id and id > 0):
				su = self.db.query(objects.Setup).get(int(id))
				try:
					if(hasattr(self, "channel")):
						su.active_state = False
						self.db.commit()
						ooff = { 'active_state': False }
						self.channel.basic_publish(exchange=utils.EXCHANGE, routing_key=utils.QUEUE_ON_OFF, body=json.dumps(ooff))
					else:
						return {'status':'error', 'message': "Error activating %s! No connection to queue server!"%su.name }
						
				except Exception as e:
					su.active_state = True;
					self.db.commit()
					return {'status':'error', 'message': "Error activating! %s"%e }
				else:
					return {'status': 'success', 'message': "Deactivated setup %s!"%su.name}
			
			return {'status':'error', 'message': "Invalid ID!" }
		
		return {'status': 'error', 'message': 'No data recieved!'}

	@cherrypy.expose
	@cherrypy.tools.json_in()
	@cherrypy.tools.json_out(handler=utils.json_handler)
	def change_login(self):
		if(hasattr(cherrypy.request, 'json')):
			username = cherrypy.request.json['username']
			password = cherrypy.request.json['password']
			try:
				exit_code = subprocess.call(["./create_htdigest.sh", PROJECT_PATH+"/webinterface/.htdigest", username, password])
				if not exit_code: # successful
					return {'status': 'success', 'message': "Login credentials have been changed!"}
				else: # exit_code != 0
					return {'status':'error', 'message': "Error changing login credentials!"}
			except Exception as e:
				return {'status':'error', 'message': "Error changing login credentials: %s"%e }


def run():
	db_log_file_name = '/var/log/secpi/db.log'
	
	db_handler = logging.FileHandler(db_log_file_name)
	db_handler.setLevel(logging.WARN)

	db_logger = logging.getLogger('sqlalchemy')
	db_logger.addHandler(db_handler)
	db_logger.setLevel(logging.WARN)
	
	cherrypy.tools.db = SQLAlchemyTool()
	cherrypy.tools.lookup = MakoTemplateTool('%s/webinterface/templates'%(PROJECT_PATH))
	
	
	cherrypy.config.update({
		'server.socket_host': '0.0.0.0',
		'server.socket_port': 8443,
		'server.ssl_module':'pyopenssl',
		'server.ssl_certificate':'%s/certs/%s'%(PROJECT_PATH, config.get("server_cert")),
		'server.ssl_private_key':'%s/certs/%s'%(PROJECT_PATH, config.get("server_key")),
		'server.ssl_certificate_chain':'%s/certs/%s'%(PROJECT_PATH, config.get("server_ca_chain")),
		'log.error_file': "/var/log/secpi/webui.log",
		'log.access_file': "/var/log/secpi/webui_access.log",
		'log.screen': False
	})
	
	app_config = {
		'/': {
			'tools.db.on': True,
			'tools.lookup.on': True,
			'tools.staticdir.root': os.path.join(PROJECT_PATH, "webinterface"),
			'tools.auth_digest.on': True,
			'tools.auth_digest.realm': 'secpi',
			'tools.auth_digest.get_ha1': auth_digest.get_ha1_file_htdigest('%s/webinterface/.htdigest'%(PROJECT_PATH)),
			'tools.auth_digest.key': 'ae41349f9413b13c'
		},
		'/static': {
			'tools.staticdir.on': True,
			'tools.staticdir.dir': 'static'
		},
		 "/favicon.ico":
		{
		  "tools.staticfile.on": True,
		  "tools.staticfile.filename": PROJECT_PATH+"/webinterface/favicon.ico"
		}
	}
	cherrypy.tree.mount(Root(), '/', app_config)
	dbfile = "%s/data.db"%PROJECT_PATH

	if not os.path.exists(dbfile):
		open(dbfile, 'w+').close()

	sqlalchemy_plugin = SQLAlchemyPlugin(
		cherrypy.engine, objects.Base, 'sqlite:///%s' % (dbfile),
		echo=False
	)
	sqlalchemy_plugin.subscribe()
	sqlalchemy_plugin.create()
	
	cherrypy.engine.start()
	cherrypy.engine.block()


if __name__ == '__main__':
	run()
