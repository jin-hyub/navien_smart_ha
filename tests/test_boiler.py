"""NR-40D discovery, capture routing, bounds and privacy checks."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from harness import Report
from navien_smarthome.boiler import BoilerDevice, boiler_event_shape, extract_boiler_event
from navien_smarthome.mqtt import NavienSmartMqtt
from navien_smarthome.coordinator import NavienSmartCoordinator
from navien_smarthome.diagnostics import _identifiers, _scrub

r = Report()
raw = {
    "serviceCode": 100, "deviceId": "test-device-only", "modelName": "NR-40D",
    "modelCode": "003", "Properties": {"did": {"response": {"feature": {
        "insideTemperatureMin": 10, "insideTemperatureMax": 40,
    }}}},
}
device = BoilerDevice.parse(raw)
r.ok(device is not None and device.feature["insideTemperatureMin"] == 10, "NR-40D discovery")
r.ok(BoilerDevice.parse({**raw, "modelName": "NR-67D"}) is None, "other models remain unsupported")
r.ok(BoilerDevice.parse({**raw, "deviceId": ""}) is None, "missing identifier rejected")
event = extract_boiler_event(json.dumps({"payload": {
    "temperature": 22, "unknownFlag": 2, "macAddress": "private-mac",
    "clientID": "private-client", "sessionID": 123456789,
    "deviceSeq": 123, "token": "private-token", "name": "private-name",
}}).encode())
r.ok(event == {"payload": {"temperature": 22, "unknownFlag": 2}}, "numeric fields only; identifiers removed")
r.ok(extract_boiler_event(b'{"payload":"{\\"temperature\\":22,\\"sessionID\\":123}"}') == {"payload": {"temperature": 22}}, "JSON string envelopes decoded with redaction")
shape = boiler_event_shape(b'{"payload":{"status":"sample-secret","macAddress":"private-mac"}}')
r.ok("sample-secret" not in json.dumps(shape) and "private-mac" not in json.dumps(shape), "structural diagnostics never include string values")
r.ok(boiler_event_shape(b'invalid')["format"] == "non_json", "non JSON format is visible")
for payload in (b'invalid', b'[]', b'null', b'x' * 65537):
    r.ok(extract_boiler_event(payload) is None, "malformed or oversized event rejected")

captured = []
hass = SimpleNamespace(loop=SimpleNamespace(call_soon_threadsafe=lambda fn, *args: fn(*args)))
mqtt = NavienSmartMqtt(hass, 1, 1, {"smarttok"}, None, None, on_boiler_event=captured.append)
r.ok(mqtt.topics == ["1/smarttok/#"], "subscribe only to selected home")
mqtt._on_message(None, None, SimpleNamespace(topic="1/smarttok/test", payload=b'{"payload":{"status":2}}'))
r.ok(captured == [{"payload": {"status": 2}}], "boiler message routes to capture, not mat state parser")
r.ok(mqtt.stats.get("mate_received", 0) == 0, "mat counters untouched")

coordinator = object.__new__(NavienSmartCoordinator)
coordinator.boiler_events = []
coordinator.boiler_event_count = 0
coordinator.data = {}
coordinator.async_update_listeners = lambda: None
for index in range(30):
    coordinator._handle_boiler_event({"value": index})
r.ok(len(coordinator.boiler_events) == 20 and coordinator.boiler_event_count == 30, "capture bounded at 20 events")
r.ok(coordinator.boiler_events[0] == {"value": 10}, "oldest event evicted")
secrets = [{"clientID": "private-client", "sessionID": "private-session", "macAddress": "private-mac"}]
scrubbed = _scrub({"nested": "private-client/private-session/private-mac"}, _identifiers(secrets))
r.ok("private" not in json.dumps(scrubbed), "nested identifier values scrubbed")
sys.exit(r.finish())
