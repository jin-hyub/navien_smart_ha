"""Current room temperature follows observed status, independent of setpoints."""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import patch

from harness import Report
from ha_stub import Base
from navien_smarthome.boiler import BoilerDevice
from navien_smarthome.coordinator import NavienSmartCoordinator
from navien_smarthome.sensor import BoilerCurrentTemperatureSensor, async_setup_entry

r = Report()
raw = {"serviceCode": 100, "modelName": "NR-40D", "modelCode": "003",
       "deviceId": "AABBCCDDEEFF1234", "connected": 1}
device = BoilerDevice.parse(raw)
co = object.__new__(NavienSmartCoordinator)
co.boilers = {device.device_id: device}; co.airone = {}; co.data = {}
co._mqtt = SimpleNamespace(connected=True)
co.async_update_listeners = lambda: None
entities = []
asyncio.run(async_setup_entry(None, SimpleNamespace(runtime_data=co), entities.extend))
sensor = next(entity for entity in entities if isinstance(entity, BoilerCurrentTemperatureSensor))
sensor.coordinator = co
r.ok(sensor.native_value is None, "sensor exists before first status without inventing a temperature")
r.ok(sensor._attr_device_class == "temperature" and sensor._attr_native_unit_of_measurement == "°C"
     and sensor._attr_state_class == "measurement", "room temperature supports HA measurement history")
for version, mode in enumerate((4, 5, 6), 1):
    co._handle_boiler_status("aabbccddeeff", version, {
        "operationMode": mode, "insideTemperature": 23.5, "insideTemperatureSetting": 28,
        "ondolTemperatureSetting": 55, "hotWaterTemperatureSetting": 45,
    })
    with patch.object(Base, "available", True, create=True):
        r.ok(sensor.available and sensor.native_value == 23.5,
             "actual temperature remains available in away, indoor and ondol modes")
co._handle_boiler_status("aabbccddeeff", 4, {"insideTemperatureSetting": 30})
r.ok(sensor.native_value == 23.5, "setpoint-only report does not replace measured temperature")
co._handle_boiler_status("aabbccddeeff", 5, {"insideTemperature": 24})
r.ok(sensor.native_value == 24, "new device measurement updates sensor")
replacement = BoilerDevice.parse(raw)
replacement.status = {"insideTemperature": 0}
co.boilers[device.device_id] = replacement
r.ok(sensor.native_value == 0, "device list refresh resolves new object and preserves zero degrees")
for value in (None, True, "24", float("nan"), float("inf")):
    replacement.status["insideTemperature"] = value
    r.ok(sensor.native_value is None, "invalid measurement is unknown")
with patch.object(Base, "available", True, create=True):
    replacement.raw = {**raw, "connected": 0}
    r.ok(not sensor.available, "offline device is unavailable")
    co.boilers.clear()
    r.ok(not sensor.available and sensor.native_value is None, "removed device is handled")
sys.exit(r.finish())
