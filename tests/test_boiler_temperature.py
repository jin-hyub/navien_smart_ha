"""Temperature UI to REST command and MQTT readback; no physical device calls."""
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
from navien_smarthome.boiler import BoilerDevice
from navien_smarthome.coordinator import NavienSmartCoordinator
from navien_smarthome.number import BoilerTemperatureNumber, async_setup_entry

r = Report()
raw = {
    "serviceCode": 100, "deviceSeq": 42, "modelName": "NR-40D", "modelCode": "003",
    "deviceId": "AABBCCDDEEFF1234", "mqttTopicKey": "test/topic", "connected": 1,
    "Properties": {"did": {"response": {"feature": {
        "insideTemperatureMin": 10, "insideTemperatureMax": 40,
        "insideTemperatureIncrementUnitType": 1,
    }}}},
}


async def check():
    device = BoilerDevice.parse(raw)
    device.status = {"operationMode": 5, "insideTemperatureSetting": 24}
    device.status_stamp = time.monotonic()
    co = object.__new__(NavienSmartCoordinator)
    co.boilers = {device.device_id: device}
    co.airone = {}; co.data = {}; co.hass = object(); co.home_seq = 11
    co._mqtt = SimpleNamespace(connected=True, client_id="test-client")
    co.async_update_listeners = lambda: None
    api = object.__new__(NavienSmartApi)
    api._require_session = lambda: SimpleNamespace(user_seq=7)
    requests = []

    async def request(*args, **kwargs):
        requests.append(json.loads(kwargs["raw_body"])["payload"])

    api._async_authed_request = request
    co.api = api
    entities = []
    await async_setup_entry(None, SimpleNamespace(runtime_data=co), entities.extend)
    r.ok(len(entities) == 1, "temperature number created for NR-40D")
    entity = entities[0]
    entity.coordinator = co  # HA stub does not bind CoordinatorEntity.
    r.ok(isinstance(entity, BoilerTemperatureNumber)
         and (entity.native_min_value, entity.native_max_value) == (10, 40),
         "UI uses device temperature bounds")
    with patch.object(Base, "available", True, create=True):
        r.ok(entity.available, "indoor heating enables temperature input")
        for value in (25, 10, 40):
            await entity.async_set_native_value(value)
            r.ok(requests[-1]["request"]["mode"] == "inside-heat"
                 and requests[-1]["request"]["param"] == [value],
                 "temperature changes send even when already in indoor mode")
        r.ok(entity.native_value == 24, "HTTP success does not claim temperature changed")
        r.ok(device.command_log[-1]["temperature"] == 40, "requested temperature recorded")
        co._handle_boiler_status("aabbccddeeff", 201, {"insideTemperatureSetting": 40})
        r.ok(entity.native_value == 40, "reported temperature updates UI")
        for value in (None, True, float("nan"), float("inf"), 9, 41, 24.5, "25"):
            count = len(requests)
            try:
                await entity.async_set_native_value(value)
                r.ok(False, "invalid temperature rejected")
            except HomeAssistantError:
                r.ok(len(requests) == count, "invalid temperature never sent")
        device.status["operationMode"] = 4
        r.ok(not entity.available, "away disables temperature input")
        count = len(requests)
        try:
            await entity.async_set_native_value(25)
            r.ok(False, "away temperature command rejected")
        except HomeAssistantError:
            r.ok(len(requests) == count, "automation cannot leave away by changing temperature")
        device.status["operationMode"] = 5
        device.raw = {**raw, "connected": 0}
        r.ok(not entity.available, "offline device disables temperature input")
        try:
            await entity.async_set_native_value(25)
            r.ok(False, "offline temperature command rejected")
        except HomeAssistantError:
            r.ok(len(requests) == count, "offline temperature never sent")
        device.raw = raw
        device.status_stamp = time.monotonic() - 1801
        try:
            await entity.async_set_native_value(25)
            r.ok(False, "stale temperature command rejected")
        except HomeAssistantError:
            r.ok(requests[-1]["requestTopic"].endswith("/status"),
                 "stale state triggers read only refresh")

    for changes in ({"insideTemperatureIncrementUnitType": 2},
                    {"insideTemperatureMin": None}, {"insideTemperatureMax": float("inf")},
                    {"insideTemperatureMin": True}, {"insideTemperatureMin": 41}):
        unsupported = deepcopy(raw)
        unsupported["Properties"]["did"]["response"]["feature"].update(changes)
        co.boilers = {device.device_id: BoilerDevice.parse(unsupported)}
        entities = []
        await async_setup_entry(None, SimpleNamespace(runtime_data=co), entities.extend)
        r.ok(not entities, "unknown encoding or invalid bounds do not create temperature UI")


asyncio.run(check())
sys.exit(r.finish())
