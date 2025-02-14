# -*- coding: utf-8 -*-
import json
import sys
import os
import traceback
import threading, requests
from Plugins.Extensions.archivCZSK.engine.service_helper import StartAddonServiceHelper, AddonServiceHelper
from Plugins.Extensions.archivCZSK.engine.tools.bouquet_generator import BouquetGeneratorTemplate
from twisted.internet.defer import inlineCallbacks, returnValue
try:
	from urllib import quote
	is_py3 = False
	def py2_decode_utf8( text ):
		return text.decode('utf-8', 'ignore')

except:
	from urllib.parse import quote
	is_py3 = True
	
	def py2_decode_utf8( text ):
		return text


sys.path.append( os.path.dirname(__file__) )

try:
	import cPickle as pickle
except:
	import pickle

import base64
from time import time
from datetime import datetime, timedelta
from xml.sax.saxutils import escape
from telly import TellyCache

try:
	from md5 import new as md5
except:
	from hashlib import md5

data_mtime = 0

EPG_GENERATOR_RUN_TIME=3600

NAME_PREFIX="telly"
NAME = "Telly"
ADDON_NAME='plugin.video.telly'

SERVICEREF_SID_START = 0xA000
SERVICEREF_TID = 7
SERVICEREF_ONID = 4
SERVICEREF_NAMESPACE = 0xE040000

XMLEPG_DATA_FILE = '%s.data.xml' % NAME_PREFIX
XMLEPG_CHANNELS_FILE = '%s.channels.xml' % NAME_PREFIX

# settings for EPGImport

EPGIMPORT_SOURCES_FILE = '/etc/epgimport/%s.sources.xml' % NAME_PREFIX
EPGIMPORT_SETTINGS_FILE = '/etc/enigma2/epgimport.conf'

EPGIMPORT_SOURCES_CONTENT ='''<?xml version="1.0" encoding="utf-8"?>
<sources>
  <mappings>
	  <channel name="{}.channels.xml">
		<url>%s</url>
	  </channel>
  </mappings>
  <sourcecat sourcecatname="{}">
	<source type="gen_xmltv" channels="{}.channels.xml">
	  <description>{}</description>
	  <url>%s</url>
	</source>
  </sourcecat>
</sources>
'''.format( NAME_PREFIX, NAME, NAME_PREFIX, NAME )

# parameters for EPGLoad

EPGLOAD_SOURCES_FILE = '/etc/epgload/.sources.xml'
EPGLOAD_SETTINGS_FILE = '/etc/epgload/epgimport.conf'

EPGLOAD_SOURCES_CONTENT ='''<?xml version="1.0" encoding="utf-8"?>
<sources>
	<sourcecat sourcecatname="{} XMLTV">
		<source type="gen_xmltv" nocheck="1" channels="//localhost%s">
			<description>{}</description>
			<url>//localhost%s</url>
		</source>
	</sourcecat>
</sources>
'''.format( NAME, NAME )

# #################################################################################################

try:
	import unidecode
	
	def strip_accents(s):
		return unidecode.unidecode(s)
except:
	import unicodedata
	
	def strip_accents(s):
		return ''.join(c for c in unicodedata.normalize('NFD', py2_decode_utf8(s)) if unicodedata.category(c) != 'Mn')

# #################################################################################################

class TellyBouquetGenerator(BouquetGeneratorTemplate):
	def __init__(self, endpoint):
		# configuration to make this class little bit reusable also in other addons
		self.prefix = NAME_PREFIX
		self.name = NAME
		self.sid_start = SERVICEREF_SID_START
		self.tid = SERVICEREF_TID
		self.onid = SERVICEREF_ONID
		self.namespace = SERVICEREF_NAMESPACE
		BouquetGeneratorTemplate.__init__(self, endpoint)

# #################################################################################################

def init_telly( settings ):
	profile_dir = '/usr/lib/enigma2/python/Plugins/Extensions/archivCZSK/resources/data/%s' % ADDON_NAME
	
	return TellyCache.get( profile_dir, service_helper.logInfo )

# #################################################################################################

def create_xmlepg( telly, data_file, channels_file, days ):
	
	fromts = int(time())
	tots = (int(time()) + (days * 86400))

	epg_ids = [channel.epg_id for channel in telly.get_channel_list()]
	epg_data = telly.get_channels_epg( epg_ids, fromts, tots)

	if not epg_data:
		raise Exception("Failed to get EPG from server")
	
	# save 1 day to epg cache for usage in the addon
	telly.fill_epg_cache(epg_ids, 24, epg_data)

	with open( channels_file, "w" ) as fc:
		with open( data_file, "w" ) as f:
			fc.write('<?xml version="1.0" encoding="UTF-8"?>\n')
			fc.write('<channels>\n')
			
			f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
			f.write('<tv generator-info-name="%s" generator-info-url="https://%s.cz" generator-info-partner="none">\n' % (ADDON_NAME, NAME_PREFIX))

			for channel in telly.get_channel_list():
				service_helper.logInfo("Processing EPG for channel: %s" % channel.name)

				id_content = NAME + '_' + strip_accents(channel.name).replace(' ', '_').replace('"','').replace(':','').replace('/','').replace('.','').replace('&','')
				fc.write( ' <channel id="%s">1:0:1:%X:%X:%X:%X:0:0:0:http%%3a//</channel>\n' % (id_content, SERVICEREF_SID_START + channel.id, SERVICEREF_TID, SERVICEREF_ONID, SERVICEREF_NAMESPACE))

				if str(channel.epg_id) not in epg_data:
					continue
				
				epg = epg_data[str(channel.epg_id)]
				
				for event in epg:
					try:
						if event["name"].startswith('Vysílání od: '):
							continue

						xml_data = {
							'start': datetime.utcfromtimestamp( event['timestamp_start'] ).strftime('%Y%m%d%H%M%S') + ' 0000',
							'stop': datetime.utcfromtimestamp( event['timestamp_end'] ).strftime('%Y%m%d%H%M%S') + ' 0000',
							'title': escape(str(event['name'])),
							'desc': escape(event['description_broadcast']) if event['description_broadcast'] != None else None
						}
						f.write( ' <programme start="%s" stop="%s" channel="%s">\n' % (xml_data['start'], xml_data['stop'], id_content ) )
						f.write( '	<title lang="cs">%s</title>\n' % xml_data['title'])
						f.write( '	<desc lang="cs">%s</desc>\n' % xml_data['desc'] )
						f.write( ' </programme>\n')
					except:
						service_helper.logException( traceback.format_exc())
						pass
						
			fc.write('</channels>\n')
			f.write('</tv>\n')
			telly.save_epg_cache()
			# save some memory
			telly.epg_cache = {}
			
# #################################################################################################

def generate_xmlepg_if_needed(settings):
	global generator_running, data_mtime
	
	# check if epgimport plugin exists
	epgimport_check_file = '/usr/lib/enigma2/python/Plugins/Extensions/EPGImport/__init__.py'

	if os.path.exists( epgimport_check_file ) or os.path.exists( epgimport_check_file + 'o' ) or os.path.exists( epgimport_check_file + 'c' ):
		epgimport_found = True
	else:
		service_helper.logDebug("EPGImport plugin not detected")
		epgimport_found = False

	# check if epgimport plugin exists
	epgload_check_file = '/usr/lib/enigma2/python/Plugins/Extensions/EPGLoad/__init__.py'

	if os.path.exists( epgload_check_file ) or os.path.exists( epgload_check_file + 'o' ) or os.path.exists( epgload_check_file + 'c' ):
		epgload_found = True
	else:
		service_helper.logDebug("EPGLoad plugin not detected")
		epgload_found = False
	
	if not epgimport_found and not epgload_found:
		service_helper.logInfo("Neither EPGImport nor EPGLoad plugin not detected")
		return
	
		
	# create paths to export files
	data_file = os.path.join(settings['xmlepg_dir'], XMLEPG_DATA_FILE)
	channels_file = os.path.join(settings['xmlepg_dir'], XMLEPG_CHANNELS_FILE)
	
	# check modification time of last exported file
	try:
		if data_mtime == 0:
			# data_mtime is global to prevent rotary disc to start by every check
			data_mtime = os.path.getmtime( data_file )
			
		if (data_mtime + 82800) >= time():
			# we have generated data file less then 23 hours
			return
	except:
		pass
	
	telly = init_telly( settings )
	if not telly or not telly.device_token:
		service_helper.logInfo("Device not paired to Telly or pairing is not valid anymore")
		return

	# time to generate new XML EPG file
	try:
		gen_time_start = time()
		create_xmlepg(telly, data_file, channels_file, int(settings['xmlepg_days']))
		data_mtime = time()
		service_helper.logInfo("EPG generated in %d seconds" % int(data_mtime - gen_time_start))
	except Exception as e:
		service_helper.logError("Something's failed by generating epg")
		service_helper.logException(traceback.format_exc())
		return

	# generate proper sources file for EPGImport
	if epgimport_found and not os.path.exists('/etc/epgimport'):
		os.mkdir( '/etc/epgimport')

	# generate proper sources file for EPGLoad
	if epgload_found and not os.path.exists('/etc/epgload'):
		os.mkdir( '/etc/epgload')
	
	epgplugin_data_list = []
	
	if epgimport_found:
		epgplugin_data_list.append( (EPGIMPORT_SOURCES_CONTENT, EPGIMPORT_SOURCES_FILE, EPGIMPORT_SETTINGS_FILE) )

	if epgload_found:
		epgplugin_data_list.append( (EPGLOAD_SOURCES_CONTENT, EPGLOAD_SOURCES_FILE, EPGLOAD_SETTINGS_FILE) )

	for epgplugin_data in epgplugin_data_list:
		xmlepg_source_content = epgplugin_data[0] % (channels_file, data_file)
		xmlepg_source_content_md5 = md5( xmlepg_source_content.encode('utf-8') ).hexdigest()
	
		# check for correct content of sources file and update it if needed
		if not os.path.exists( epgplugin_data[1] ) or md5( open( epgplugin_data[1], 'rb' ).read() ).hexdigest() != xmlepg_source_content_md5:
			service_helper.logDebug("Writing new sources file to " + epgplugin_data[1] )
			with open( epgplugin_data[1], 'w' ) as f:
				f.write( xmlepg_source_content )
	
		# check if source is enabled in epgimport settings and enable if needed
		if os.path.exists( epgplugin_data[2] ):
			epgimport_settings = pickle.load(open(epgplugin_data[2], 'rb'))
		else:
			epgimport_settings = { 'sources': [] }
	
		if NAME not in epgimport_settings['sources']:
			service_helper.logInfo("Enabling %s in epgimport/epgload config %s" % (NAME, epgplugin_data[2]) )
			epgimport_settings['sources'].append(NAME)
			pickle.dump(epgimport_settings, open(epgplugin_data[2], 'wb'), pickle.HIGHEST_PROTOCOL)


# #################################################################################################

def print_settings( settings ):
	return service_helper.logDebug("Received settings: %s" % settings)

# #################################################################################################

epg_generator_running = False

def start_epg_generator(arg):
	if int(time()) < 1650358276:
		# time is not synced yet - wait a little bit and try again
		return service_helper.runDelayed(10, start_epg_generator, None )
	
	global epg_generator_running
	
	if epg_generator_running:
		# do nothing
		return
	
	epg_generator_running = True
	# load actual settings and continue when received
	service_helper.getSettings(['enable_userbouquet', 'enable_xmlepg', 'xmlepg_dir', 'xmlepg_days'], settings_received_epg )
	
# #################################################################################################

def epg_generator_stop( settings ):
	global epg_generator_running
	epg_generator_running = False

# #################################################################################################

def settings_received_epg( settings ):
	# check received settings
	print_settings( settings )
	
	if not settings['enable_xmlepg'] or not settings['enable_userbouquet']:
		epg_generator_running = False
		service_helper.logDebug("Generating of XMLEPG is disabled")
		service_helper.runDelayed(EPG_GENERATOR_RUN_TIME, start_epg_generator, None )
		return

	if not settings['xmlepg_dir']:
		epg_generator_running = False
		service_helper.logError("No destination directory for XMLEPG is set")
		service_helper.runDelayed(EPG_GENERATOR_RUN_TIME, start_epg_generator, None )
		return
	
	service_helper.runDelayed(1, (generate_xmlepg_if_needed, epg_generator_stop), settings )
	service_helper.runDelayed(EPG_GENERATOR_RUN_TIME, start_epg_generator, None )

# #################################################################################################	

bouquet_generator_running = False

def start_bouquet_generator(arg):
	act_time = int(time())
	
	if act_time < 1650358276:
		if act_time < 3600:
			# time is not synced yet - wait a little bit and try again
			return service_helper.runDelayed(10, start_bouquet_generator, None )
		else:
			return
	
	try_generate_userbouquet( (False,False) )

# #################################################################################################

def bouquet_generator_stop( data ):
	global bouquet_generator_running
	bouquet_generator_running = False

# #################################################################################################

def service_stop( data ):
	service_helper.logInfo("Stopping service")
	service_helper.service_stop()
	
# #################################################################################################

def try_generate_userbouquet( data ):
	force, stop_service = data
	global bouquet_generator_running
	
	if bouquet_generator_running:
		# do nothing
		return

	bouquet_generator_running = True
	service_helper.getHttpEndpoint( ADDON_NAME, http_endpoint_received, force=force, stop_service=stop_service )
	
# #################################################################################################

def http_endpoint_received( addon_id, endpoint, force, stop_service ):
	service_helper.logDebug("%s HTTP endpoint received: %s" % (addon_id, endpoint))
	# load actual settings and continue when received
	service_helper.getSettings(['enable_userbouquet', 'enable_adult', 'enable_xmlepg', 'player_name', 'enable_picons'], settings_received_bouquet, endpoint=endpoint, force=force, stop_service=stop_service )

# #################################################################################################

def settings_received_bouquet( settings, endpoint, force, stop_service ):
	# check received settings
	print_settings( settings )
	
	if stop_service:
		service_helper.runDelayed(1, (generate_userbouquet, bouquet_generator_stop, service_stop), (settings, endpoint, force) )
	else:
		service_helper.runDelayed(1, (generate_userbouquet, bouquet_generator_stop), (settings, endpoint, force) )

# #################################################################################################

def remove_userbouquet(endpoint):
	obg = TellyBouquetGenerator( endpoint )
	service_helper.logDebug("Removing userbouquet")
	if obg.userbouquet_remove():
		service_helper.logDebug("Userbouquet removed")

# #################################################################################################

def generate_userbouquet( data ):
	settings, endpoint, force = data
	
	if not settings['enable_userbouquet']:
		remove_userbouquet(endpoint)
		return

	telly = init_telly( settings )
	
	if not telly or not telly.device_token:
		service_helper.logInfo("Device not paired to Telly or pairing expired")
		remove_userbouquet(endpoint)
		return

	obg = TellyBouquetGenerator( endpoint )

	try:
		channels = []
		service_helper.logDebug("Requesting channel list for userbouquet generator")
		for channel in telly.get_channel_list():
			channels.append({
					'id': channel.id,
					'key': "%d" % channel.id,
					'name': channel.name,
					'adult': channel.adult,
					'picon': channel.picon
				}) 

		service_helper.logDebug("Starting generating of userbouquet")
		if obg.generate_bouquet( channels, settings['enable_adult'], settings['enable_xmlepg'], settings['enable_picons'], settings['player_name'] ):
			service_helper.logDebug("Userbouquet successfuly generated")
		else:
			service_helper.logDebug("No need to regenerate userbouquet")
	except:
		if force:
			msg = "Pri generovaní userbouquetu pre Telly nastala chyba. Skontrolujte log súbor a zareportujte chybu."
			service_helper.showErrorMessage( msg )
			
		service_helper.logException( traceback.format_exc())
	
	
# #################################################################################################	
	
class TellyAddonServiceHelper( AddonServiceHelper ):
	def handle_userbouquet_gen(self):
		self.runDelayed(1, try_generate_userbouquet, (True, False) )
	
	def handle_stop(self):
		self.runDelayed(1, try_generate_userbouquet, (True, True) )
	
# #################################################################################################

service_helper = StartAddonServiceHelper(TellyAddonServiceHelper(), start_epg_generator, None)
#service_helper.runDelayed(1, start_bouquet_generator, None )
service_helper.runLoop( 4*3600, start_bouquet_generator, None )
service_helper.run()
