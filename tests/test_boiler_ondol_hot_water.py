"""NR-40D ondol and independent hot-water settings, without hardware calls.

Protocol evidence: Navien Smart 2.10.1 (2432), package
kr.co.kdnavien.naviensmart. SmartTokControlFragment.executeJob uses
ondol-heat with [ondolTemperatureSetting]; setDataObserver$4 uses
hotwater-temperature with [selectedTemperature]. BoilerConstants routes
003 through boilerNR30SeriesMqttPayload / RequestControlDotWifi.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from harness import Report
from ha_stub import Base
from homeassistant.exceptions import HomeAssistantError
from navien_smarthome.api import NavienSmartApi
from navien_smarthome.boiler import BoilerDevice, build_boiler_request
from navien_smarthome.coordinator import NavienSmartCoordinator
from navien_smarthome.number import async_setup_entry
from navien_smarthome.select import BoilerModeSelect

r = Report()
raw = {
    "serviceCode": 100, "deviceSeq": 42, "modelName": "NR-40D", "modelCode": "003",
    "deviceId": "AABBCCDDEEFF1234", "mqttTopicKey": "test/topic", "connected": 1,
    "Properties": {"did": {"response": {"feature": {
        "insideTemperatureMin": 10, "insideTemperatureMax": 40,
        "insideTemperatureIncrementUnitType": 1,
        "ondolTemperatureMin": 40, "ondolTemperatureMax": 83,
        "ondolTemperatureIncrementUnitType": 1,
        "hotWaterTemperatureMin": 30, "hotWaterTemperatureMax": 60,
        "hotWaterTemperatureIncrementUnitType": 1,
    }}}},
}

for kwargs, command, value in [
    ({"mode": 6, "temperature": 40}, "ondol-heat", 40),
    ({"mode": 6, "temperature": 83}, "ondol-heat", 83),
    ({"hot_water_temperature": 30}, "hotwater-temperature", 30),
    ({"hot_water_temperature": 60}, "hotwater-temperature", 60),
]:
    payload = build_boiler_request(raw, "test-client", **kwargs)["payload"]
    r.ok(payload["request"]["mode"] == command and payload["request"]["param"] == [value]
         and payload["requestTopic"].endswith("/control"), "app command and temperature encoding")

for kwargs in [
    {"mode": 6}, {"mode": 6, "temperature": 39}, {"mode": 6, "temperature": 84},
    {"mode": 6, "temperature": 50.5}, {"hot_water_temperature": 29},
    {"hot_water_temperature": 61}, {"hot_water_temperature": 45.5},
    {"hot_water_temperature": True}, {"hot_water_temperature": float("nan")},
    {"hot_water_temperature": float("inf")}, {"hot_water_temperature": "45"},
    {"mode": 4, "hot_water_temperature": 45}, {"mode": 5, "temperature": 24, "hot_water_temperature": 45},
    {"temperature": 45},
]:
    try:
        build_boiler_request(raw, "test-client", **kwargs)
        r.ok(False, "invalid or mixed command rejected")
    except ValueError:
        r.ok(True, "invalid or mixed command rejected")


async def check():
    device = BoilerDevice.parse(raw)
    device.status = {"operationMode": 5, "insideTemperatureSetting": 24,
                     "ondolTemperatureSetting": 40, "hotWaterTemperatureSetting": 45}
    device.status_stamp = time.monotonic()
    co = object.__new__(NavienSmartCoordinator)
    co.boilers = {device.device_id: device}
    co.airone = {}; co.data = {}; co.hass = object(); co.home_seq = 11
    co._mqtt = SimpleNamespace(connected=True, client_id="test-client")
    co.async_update_listeners = lambda: None
    api = object.__new__(NavienSmartApi)
    api._require_session = lambda: SimpleNamespace(user_seq=7)
    requests, readbacks = [], []

    async def request(*args, **kwargs):
        requests.append(json.loads(kwargs["raw_body"])["payload"])

    api._async_authed_request = request
    co.api = api
    entities = []
    await async_setup_entry(None, SimpleNamespace(runtime_data=co), entities.extend)
    r.ok(len(entities) == 3, "three independent temperature inputs created")
    numbers = {entity._kind: entity for entity in entities}
    for entity in entities:
        entity.coordinator = co
    inside, ondol, water = (numbers[k] for k in ("inside", "ondol", "hot_water"))
    select = object.__new__(BoilerModeSelect)
    select.coordinator = co; select._device_id = device.device_id
    r.ok("온돌난방" in select._attr_options, "mode selector includes ondol")
    r.ok((water.native_min_value, water.native_max_value, water._attr_native_step) == (30, 60, 1),
         "hot-water UI exposes 30 to 60 Celsius in single degrees")
    r.ok((ondol.native_min_value, ondol.native_max_value) == (40, 83), "ondol UI uses its own bounds")
    r.ok(inside._attr_unique_id.endswith("_boiler_inside_temperature"), "existing indoor entity ID preserved")

    with patch.object(Base, "available", True, create=True), patch(
        "navien_smarthome.coordinator.async_call_later",
        side_effect=lambda hass, delay, callback: readbacks.append(callback),
    ):
        r.ok(inside.available and not ondol.available and water.available, "indoor mode enables indoor and water inputs")
        await select.async_select_option("온돌난방")
        r.ok(requests[-1]["request"]["mode"] == "ondol-heat"
             and requests[-1]["request"]["param"] == [40], "switch to ondol preserves observed ondol target")
        r.ok(select.current_option == "실내난방", "HTTP acknowledgement leaves mode unchanged")
        co._handle_boiler_status("aabbccddeeff", 201, {"operationMode": 6})
        r.ok(select.current_option == "온돌난방" and ondol.available and not inside.available,
             "ondol report updates mode and enables ondol input")
        await ondol.async_set_native_value(55)
        r.ok(requests[-1]["request"]["param"] == [55] and ondol.native_value == 40,
             "ondol change sends requested target and waits for report")
        co._handle_boiler_status("aabbccddeeff", 202, {"ondolTemperatureSetting": 55})
        r.ok(ondol.native_value == 55 and inside.native_value == 24 and water.native_value == 45,
             "ondol report preserves independent indoor and water targets")
        for mode in (4, 5, 6):
            co._handle_boiler_status("aabbccddeeff", 202 + mode, {"operationMode": mode})
            await water.async_set_native_value(50)
            r.ok(water.available and requests[-1]["request"]["mode"] == "hotwater-temperature"
                 and requests[-1]["request"]["param"] == [50] and device.status["operationMode"] == mode,
                 "water temperature sends independently in away, indoor and ondol modes")
        r.ok(water.native_value == 45, "water target waits for device report")
        co._handle_boiler_status("aabbccddeeff", 210, {"hotWaterTemperatureSetting": 50})
        r.ok(water.native_value == 50 and ondol.native_value == 55, "water report leaves heating target intact")
        r.ok(device.command_log[-1]["hot_water_temperature"] == 50, "water command recorded in diagnostics")
        await readbacks[-1](None)
        r.ok(requests[-1]["requestTopic"].endswith("/status") and "mode" not in requests[-1]["request"],
             "post-command readback queries status without changing mode")
        co._handle_boiler_status("aabbccddeeff", 211, {"operationMode": 4})
        count = len(requests)
        try:
            await ondol.async_set_native_value(50)
            r.ok(False, "away ondol temperature rejected")
        except HomeAssistantError:
            r.ok(len(requests) == count, "ondol temperature cannot leave away implicitly")
        device.status_stamp = time.monotonic() - 1801
        try:
            await water.async_set_native_value(50)
            r.ok(False, "stale water state rejected")
        except HomeAssistantError:
            r.ok(requests[-1]["requestTopic"].endswith("/status"), "stale water state triggers read only refresh")
        device.status_stamp = time.monotonic()
        device.raw = {**raw, "connected": 0}
        count = len(requests)
        try:
            await water.async_set_native_value(50)
            r.ok(False, "offline water command rejected")
        except HomeAssistantError:
            r.ok(not water.available and len(requests) == count, "offline water command never sent")

    for kind, prefix in (("ondol", "ondolTemperature"), ("hot_water", "hotWaterTemperature")):
        for value in (None, 0.5, True):
            unsupported = deepcopy(raw)
            unsupported["Properties"]["did"]["response"]["feature"][f"{prefix}IncrementUnitType"] = value
            co.boilers = {device.device_id: BoilerDevice.parse(unsupported)}
            created = []
            await async_setup_entry(None, SimpleNamespace(runtime_data=co), created.extend)
            r.ok(kind not in {entity._kind for entity in created}, "unsupported temperature encoding creates no input")


asyncio.run(check())
sys.exit(r.finish())
