"""Model-003 request fixtures from app analysis; no network or physical device calls."""
from __future__ import annotations
import asyncio
import json
import sys
import time
from types import SimpleNamespace
from harness import Report
from navien_smarthome.boiler import BoilerDevice, build_boiler_request, extract_boiler_status
from navien_smarthome.coordinator import NavienSmartCoordinator
from navien_smarthome.api import NavienSmartApi
from navien_smarthome.select import BoilerModeSelect
from homeassistant.exceptions import HomeAssistantError

r=Report()
raw={'serviceCode':100,'deviceSeq':42,'modelName':'NR-40D','modelCode':'003',
     'deviceId':'AABBCCDDEEFF1234','mqttTopicKey':'test-topic','connected':1,
     'Properties':{'did':{'response':{'feature':{'insideTemperatureMin':10,
         'insideTemperatureMax':40,'insideTemperatureIncrementUnitType':1}}}}}
body=build_boiler_request(raw,'uuid-U1',mode=4,now=123)
p=body['payload']
r.ok(body['serviceCode']==100 and set(p)=={'sessionID','clientID','requestTopic','responseTopic','request'},'003 uses DotWifi envelope, without protocolVersion')
r.ok(p['requestTopic']=='cmd/003/roomcon-AABBCCDDEEFF/control','control topic follows app')
r.ok(p['responseTopic']=='cmd/003/test-topic/mobile-uuid-U1/res','response topic includes key and client')
path_key = build_boiler_request({**raw, 'mqttTopicKey':'home/100/test-topic'}, 'uuid-U1', mode=4)
r.ok(path_key['payload']['responseTopic']=='cmd/003/home/100/test-topic/mobile-uuid-U1/res', 'server topic key may contain path separators')
for key, client, expected in [('', 'uuid-U1', 'mqttTopicKey'), ('valid', '', '클라이언트 ID'), ('bad/+', 'uuid-U1', '허용되지'), ('bad\x00key', 'uuid-U1', '허용되지')]:
 try:
  build_boiler_request({**raw, 'mqttTopicKey':key}, client, mode=4)
  r.ok(False, 'invalid route rejected with specific reason')
 except ValueError as error:
  r.ok(expected in str(error), 'invalid route rejected with specific reason')
r.ok(p['request']=={'macAddress':'AABBCCDDEEFF','additionalValue':'1234','registerAt':'123000','param':[],'mode':'goout-on'},'away command has empty param')
heat=build_boiler_request(raw,'uuid-U1',mode=5,temperature=24,now=123)
r.ok(heat['payload']['request']['mode']=='inside-heat' and heat['payload']['request']['param']==[24],'indoor mode preserves known setting')
status=build_boiler_request(raw,'uuid-U1',now=123)['payload']
r.ok(status['requestTopic'].endswith('/status') and 'mode' not in status['request'] and status['request']['param']==[],'startup status request does not set a mode')
for kwargs in [{'mode':6},{'mode':True},{'mode':5},{'mode':5,'temperature':float('nan')},{'mode':5,'temperature':41},{'mode':5,'temperature':23.5}]:
 try:build_boiler_request(raw,'uuid-U1',**kwargs);r.ok(False,'reject invalid command')
 except ValueError:r.ok(True,'reject invalid command')
for change in [{'modelCode':'011'},{'deviceId':'short'},{'mqttTopicKey':'bad/#'}]:
 try:build_boiler_request({**raw,**change},'uuid-U1',mode=4);r.ok(False,'reject wrong routing')
 except ValueError:r.ok(True,'reject wrong routing')
event={'payload':{'response':{'macAddress':'aabbccddeeff','registerAt':200,'status':{'operationMode':4,'insideTemperatureSetting':24}}}}
parsed=extract_boiler_status(json.dumps(event).encode())
r.ok(parsed==('aabbccddeeff',200,{'operationMode':4,'insideTemperatureSetting':24}),'incoming status parsed without treating requested values as actual state')
r.ok(extract_boiler_status(b'{"payload":{"request":{"mode":"goout-on"}}}') is None,'outgoing request is never an observed state')

async def check():
 api=object.__new__(NavienSmartApi)
 api._require_session=lambda:SimpleNamespace(user_seq=7)
 requests=[]
 async def request(*args,**kwargs):requests.append((args,kwargs))
 api._async_authed_request=request
 await api.async_boiler_request(11,raw,'uuid-U1',mode=4)
 args,kwargs=requests[0]
 r.ok(args==('POST','/devices/42/control') and kwargs['params']=={'homeSeq':11,'userSeq':7},'REST route and account scope')
 r.ok(json.loads(kwargs['raw_body'])['payload']['request']['mode']=='goout-on','REST serializes correct command')
 device=BoilerDevice.parse(raw)
 co=object.__new__(NavienSmartCoordinator)
 co.boilers={device.device_id:device};co.data={};co.async_update_listeners=lambda:None
 co._handle_boiler_status('not-this-device',999,{'operationMode':5})
 r.ok(not device.status,'unrelated device report ignored')
 co._handle_boiler_status(*parsed)
 co._handle_boiler_status('aabbccddeeff',100,{'operationMode':5})
 r.ok(device.status['operationMode']==4,'out of order status ignored')
 co._mqtt=SimpleNamespace(connected=True,client_id='uuid-U1');co.hass=object();co.home_seq=11;co.api=api
 await co.async_boiler_mode(device.device_id,5)
 r.ok(device.status['operationMode']==4,'HTTP success does not change displayed state')
 r.ok(json.loads(requests[-1][1]['raw_body'])['payload']['request']['param']==[24],'mode change reuses device target')
 r.ok(device.command_log[-1]['mode']==5,'sent mode recorded for diagnosis')
 # Stub CoordinatorEntity has no initializer logic; bind the coordinator manually.
 entity=object.__new__(BoilerModeSelect);entity.coordinator=co;entity._device_id=device.device_id
 r.ok(entity.current_option=='외출','select follows observed state')
 co._handle_boiler_status('aabbccddeeff',201,{'operationMode':5})
 r.ok(entity.current_option=='실내난방','select updates after device acknowledgement')
 device.raw={**raw,'connected':0}
 try:await co.async_boiler_mode(device.device_id,4);r.ok(False,'offline command rejected')
 except HomeAssistantError:r.ok(True,'offline command rejected')
 device.raw=raw;device.status_stamp=time.monotonic()-1801
 try:await co.async_boiler_mode(device.device_id,4);r.ok(False,'stale state requests refresh')
 except HomeAssistantError:r.ok(json.loads(requests[-1][1]['raw_body'])['payload']['requestTopic'].endswith('/status'),'stale state triggers read only request')

asyncio.run(check())
sys.exit(r.finish())
