# -*- coding: utf-8 -*-
import json
import sys
import os
import traceback
import threading, requests
from Plugins.Extensions.archivCZSK.engine.service_helper import StartAddonServiceHelper, AddonServiceHelper
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
from orangetv import OrangeTVcache, OrangeTvBouquetGenerator

try:
	from md5 import new as md5
except:
	from hashlib import md5

data_mtime = 0

EPG_GENERATOR_RUN_TIME=3600

NAME_PREFIX="orangetv"
NAME = "OrangeTV"
ADDON_NAME='plugin.video.orangetv'

SERVICEREF_SID_START = 0x100
SERVICEREF_TID = 4
SERVICEREF_ONID = 3
SERVICEREF_NAMESPACE = 0xE020000

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

def init_orangetv( settings ):
	profile_dir = '/usr/lib/enigma2/python/Plugins/Extensions/archivCZSK/resources/data/%s' % ADDON_NAME
	
	if len(settings['orangetvuser']) > 0 and len( settings['orangetvpwd'] ) > 0:
		orangetv = OrangeTVcache.get( settings['orangetvuser'], settings['orangetvpwd'], settings['deviceid'], profile_dir, service_helper.logInfo )
		orangetv.refresh_access_token()
	else:
		orangetv = None
		
	return orangetv

# #################################################################################################

def create_xmlepg( orangetv, data_file, channels_file, days ):
	
	fromts = int(time())*1000
	tots = (int(time()) + (days * 86400)) * 1000

	with open( channels_file, "w" ) as fc:
		with open( data_file, "w" ) as f:
			fc.write('<?xml version="1.0" encoding="UTF-8"?>\n')
			fc.write('<channels>\n')
			
			f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
			f.write('<tv generator-info-name="%s" generator-info-url="https://%s.cz" generator-info-partner="none">\n' % (ADDON_NAME, NAME_PREFIX))

			for channel in orangetv.live_channels():
				service_helper.logInfo("Processing EPG for channel: %s (%s)" % (channel.name, channel.channel_key))

				id_content = NAME + '_' + strip_accents(channel.channel_key).replace(' ', '_').replace('"','').replace(':','').replace('/','').replace('.','')
				fc.write( ' <channel id="%s">1:0:1:%X:%X:%X:%X:0:0:0:http%%3a//</channel>\n' % (id_content, SERVICEREF_SID_START + channel.id, SERVICEREF_TID, SERVICEREF_ONID, SERVICEREF_NAMESPACE))

				epg = orangetv.getChannelEpg( channel.channel_key, fromts, tots )
				# save 1 day to epg cache for usage in the addon
				orangetv.fillChannelEpgCache(channel.channel_key, epg, fromts + (24 * 3600 * 1000))
				
				for event in epg:
					try:
						xml_data = {
							'start': datetime.utcfromtimestamp( event['startTimestamp'] / 1000 ).strftime('%Y%m%d%H%M%S') + ' 0000',
							'stop': datetime.utcfromtimestamp( event['endTimestamp'] / 1000 ).strftime('%Y%m%d%H%M%S') + ' 0000',
							'title': escape(str(event['name'])),
							'desc': escape(event['shortDescription']) if event['shortDescription'] != None else None
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
			orangetv.saveEpgCache()
			# save some memory
			orangetv.epg_cache = {}
			
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
	
	orangetv = init_orangetv( settings )
	if orangetv == None:
		service_helper.logInfo("No orangetv login credentials provided or they are wrong")
		return

	# time to generate new XML EPG file
	try:
		gen_time_start = time()
		create_xmlepg(orangetv, data_file, channels_file, int(settings['xmlepg_days']))
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
	return service_helper.logDebug("Received cfgs: %s" % settings)

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
	service_helper.getSettings(['orangetvuser', 'orangetvpwd', 'deviceid', 'enable_xmlepg', 'xmlepg_dir', 'xmlepg_days'], settings_received_epg )
	
# #################################################################################################

def epg_generator_stop( settings ):
	global epg_generator_running
	epg_generator_running = False

# #################################################################################################

def settings_received_epg( settings ):
	# check received settings
	print_settings( settings )
	
	if not settings['enable_xmlepg']:
		epg_generator_running = False
		service_helper.logDebug("Generating of XMLEPG is disabled")
		service_helper.runDelayed(EPG_GENERATOR_RUN_TIME, start_epg_generator, None )
		return

	if not settings['orangetvuser'] or not settings['orangetvpwd'] or not settings['deviceid'] or not settings['xmlepg_dir']:
		epg_generator_running = False
		service_helper.logError("No login data provided")
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

#def bouquet_generator_stop( settings, endpoint ):
def bouquet_generator_stop( data ):
	global bouquet_generator_running
	bouquet_generator_running = False
	
# #################################################################################################

def try_generate_userbouquet( arg ):
	global bouquet_generator_running
	
	if bouquet_generator_running:
		# do nothing
		return

	bouquet_generator_running = True
	service_helper.getHttpEndpoint( ADDON_NAME, http_endpoint_received )
	
# #################################################################################################

def http_endpoint_received( addon_id, endpoint ):
	service_helper.logDebug("%s HTTP endpoint received: %s" % (addon_id, endpoint))
	# load actual settings and continue when received
	service_helper.getSettings(['orangetvuser', 'orangetvpwd', 'deviceid', 'enable_adult', 'enable_xmlepg', 'player_name', 'enable_picons'], settings_received_bouquet, endpoint )

# #################################################################################################

def settings_received_bouquet( settings, endpoint ):
	# check received settings
	print_settings( settings )
	
	if not settings['orangetvuser'] or not settings['orangetvpwd'] or not settings['deviceid']:
		bouquet_generator_running = False
		service_helper.logError("No login data provided")
		return
	
	service_helper.runDelayed(1, (generate_userbouquet, bouquet_generator_stop), (settings, endpoint) )

# #################################################################################################

def generate_userbouquet( data ):
	settings, endpoint = data
	
	orangetv = init_orangetv( settings )
	if orangetv == None:
		service_helper.logInfo("[ORANGE-XMLEPG] No orangetv login credentials provided or they are wrong")
		return

	channels = []
	for channel in orangetv.live_channels():
		channels.append({
				'id': channel.id,
				'key': channel.channel_key,
				'name': channel.name,
				'adult': channel.adult,
				'picon': channel.picon
			}) 

	try:	
		obg = OrangeTvBouquetGenerator( endpoint )
		obg.generate_bouquet( channels, settings['enable_adult'], settings['enable_xmlepg'], settings['enable_picons'], settings['player_name'])
		msg = "OrangeTV userbouquet vygenerovaný"
		service_helper.showInfoMessage( msg )
	except:
		msg = "Pri generovaní userbouquetu pre OrangeTV nastala chyba. Skontrolujte log súbor a zareportujte chybu."
		service_helper.showErrorMessage( msg )
		service_helper.logException( traceback.format_exc())
	
	
# #################################################################################################	
	
def loop_test( data ):
	service_helper.logInfo("Toto bezi v slucke: %s" % data )
	
class OrangeAddonServiceHelper( AddonServiceHelper ):
	def handle_userbouquet_gen(self):
		self.runDelayed(1, try_generate_userbouquet, None )
	
# #################################################################################################
	
service_helper = StartAddonServiceHelper(OrangeAddonServiceHelper(), start_epg_generator, None)
#service_helper.runLoop( 5, loop_test, 123 )
service_helper.run()
