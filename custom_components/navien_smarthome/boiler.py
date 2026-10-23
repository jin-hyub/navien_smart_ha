"""NR-40D (003): protocol from Navien Smart 2.10.1, hardware control unverified."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any


def numeric_tree(value: Any, depth: int = 0) -> Any:
    """Keep numeric protocol fields, never identifiers or arbitrary strings."""
    if depth > 12:
        return None
    if isinstance(value, str) and len(value) <= 65536:
        # Some MQTT envelopes contain a JSON document serialized as a string.
        # Never keep the original string, and apply the same identifier filtering.
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            return None
        if isinstance(decoded, (dict, list)):
            return numeric_tree(decoded, depth + 1)
        return None
    if isinstance(value, dict):
        result = {}
        for key, inner in list(value.items())[:128]:
            if any(part in key.lower() for part in (
                "token", "password", "client", "session", "topic", "mac", "ssid",
                "nickname", "account", "deviceid", "deviceseq", "user", "home",
            )):
                continue
            cleaned = numeric_tree(inner, depth + 1)
            if cleaned is not None:
                result[key] = cleaned
        return result or None
    if isinstance(value, list):
        return [numeric_tree(item, depth + 1) for item in value[:128]]
    if isinstance(value, (int, float, bool)):
        return value
    return None


def extract_boiler_event(payload: bytes) -> dict[str, Any] | None:
    """Capture numeric fields without assuming temperature encoding or state shape."""
    if len(payload) > 65536:
        return None
    try:
        event = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(event, dict):
        return None
    return numeric_tree(event)


def boiler_event_shape(payload: bytes) -> dict[str, Any]:
    """Bounded structural sample: field names and types, no string values."""
    if len(payload) > 65536:
        return {"format": "oversized"}
    try:
        event = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return {"format": "non_json", "bytes": len(payload)}

    def shape(value: Any, depth: int = 0) -> Any:
        if depth >= 6:
            return type(value).__name__
        if isinstance(value, dict):
            return {key: shape(inner, depth + 1) for key, inner in list(value.items())[:32]}
        if isinstance(value, list):
            return [shape(inner, depth + 1) for inner in value[:3]]
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                return "string"
            if isinstance(decoded, (dict, list)):
                return {"json_string": shape(decoded, depth + 1)}
            return "string"
        return type(value).__name__

    return {"format": "json", "shape": shape(event)}


@dataclass
class BoilerDevice:
    raw: dict[str, Any]
    status: dict[str, Any] = field(default_factory=dict)
    status_stamp: float = 0
    status_version: float = 0
    command_log: list = field(default_factory=list)

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> BoilerDevice | None:
        if str(raw.get("serviceCode")) != "100" or raw.get("modelName") != "NR-40D" or str(raw.get("modelCode")) != "003":
            return None
        if not raw.get("deviceId"):
            return None
        return cls(raw)

    @property
    def device_id(self) -> str:
        return str(self.raw["deviceId"])

    @property
    def nickname(self) -> str:
        name = (self.raw.get("Properties") or {}).get("nickName")
        return name if isinstance(name, str) and name else "NR-40D 보일러"

    @property
    def feature(self) -> dict[str, Any]:
        did = (self.raw.get("Properties") or {}).get("did") or {}
        return numeric_tree((did.get("response") or {}).get("feature")) or {}

    @property
    def inside_temperature_bounds(self) -> tuple[int, int] | None:
        return self.temperature_bounds("inside")

    def temperature_bounds(self, kind: str) -> tuple[int, int] | None:
        """Expose only the integer Celsius encoding supported by the request builder."""
        prefix = BOILER_TEMPERATURE_PREFIXES.get(kind)
        if prefix is None:
            return None
        feature = self.feature
        low, high = feature.get(f"{prefix}Min"), feature.get(f"{prefix}Max")
        increment = feature.get(f"{prefix}IncrementUnitType")
        if isinstance(increment, bool) or increment != 1:
            return None
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value != int(value) for value in (low, high)):
            return None
        return (int(low), int(high)) if low < high else None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "model_name": "NR-40D",
            "model_code": self.raw.get("modelCode"),
            "control_enabled": True,
            "hardware_control_verified": False,
            "status": dict(self.status),
            "command_log": list(self.command_log),
            "feature": self.feature,
        }


# Source: SmartTokControlFragment tab handlers and BoilerConstants
# boilerMqttPayload dispatches model 003 through boilerNR30SeriesMqttPayload
# (RequestControlDotWifi); do NOT use the similarly named NR40 method.
# SmartTokControlFragment.updateTabUI maps operationMode 6 to ondolHeatTab;
# executeJob sends ondol-heat with the observed ondolTemperatureSetting.
BOILER_MODES = {4: "외출", 5: "실내난방", 6: "온돌난방"}
BOILER_TEMPERATURE_PREFIXES = {
    "inside": "insideTemperature", "ondol": "ondolTemperature", "hot_water": "hotWaterTemperature",
}


def _temperature_param(device, kind, temperature):
    bounds = device.temperature_bounds(kind)
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or bounds is None
            or not bounds[0] <= temperature <= bounds[1]):
        raise ValueError("기기가 알려준 설정 온도 범위의 값이 필요합니다")
    if temperature != int(temperature):
        raise ValueError("설정 온도는 1°C 단위로 입력해 주세요")
    return [int(temperature)]


def build_boiler_request(raw, client_id, *, mode=None, temperature=None, hot_water_temperature=None, now=None):
    """Build only observed modes, following the app's model-003 request envelope."""
    if BoilerDevice.parse(raw) is None:
        raise ValueError("지원하지 않는 보일러 모델입니다")
    device_id = str(raw.get("deviceId") or "")
    key = str(raw.get("mqttTopicKey") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{16}", device_id):
        raise ValueError("보일러 식별자 형식을 확인할 수 없습니다")
    # The app inserts the server's topic key verbatim. It may contain multiple
    # MQTT path levels; restricting it to an identifier rejects valid devices.
    if not key:
        raise ValueError("서버 기기목록에 보일러 mqttTopicKey가 없습니다")
    if any(char in key for char in "+#") or any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise ValueError("보일러 응답 토픽에 허용되지 않는 문자가 있습니다")
    if not client_id:
        raise ValueError("MQTT 클라이언트 ID가 아직 준비되지 않았습니다")
    if mode not in (None, *BOILER_MODES) or isinstance(mode, bool):
        raise ValueError("외출, 실내난방 또는 온돌난방만 지원합니다")
    if hot_water_temperature is not None and (mode is not None or temperature is not None):
        raise ValueError("온수 온도와 난방 모드는 별도로 설정해 주세요")
    if temperature is not None and mode not in (5, 6):
        raise ValueError("난방 온도는 실내난방 또는 온돌난방과 함께 설정해 주세요")
    stamp = str(int((time.time() if now is None else now) * 1000))
    kind = "status" if mode is None and hot_water_temperature is None else "control"
    request = {"macAddress": device_id[:12], "registerAt": stamp,
               "additionalValue": device_id[12:16], "param": []}
    if mode == 4:
        request["mode"] = "goout-on"
    elif mode in (5, 6):
        temperature_kind = "inside" if mode == 5 else "ondol"
        request.update(mode="inside-heat" if mode == 5 else "ondol-heat",
                       param=_temperature_param(BoilerDevice(raw), temperature_kind, temperature))
    elif hot_water_temperature is not None:
        # SmartTokControlFragment's hot-water temperature observer sends this
        # independent command with one temperature argument, preserving operationMode.
        request.update(mode="hotwater-temperature",
                       param=_temperature_param(BoilerDevice(raw), "hot_water", hot_water_temperature))
    return {"serviceCode": 100, "payload": {
        "sessionID": stamp, "clientID": f"mobile-{client_id}",
        "requestTopic": f"cmd/003/roomcon-{device_id[:12]}/{kind}",
        "responseTopic": f"cmd/003/{key}/mobile-{client_id}/res",
        "request": request,
    }}


def extract_boiler_status(payload):
    """Retain identity only internally, never in captured diagnostics."""
    if len(payload) > 65536:
        return None
    try:
        event = json.loads(payload)
        inner = event.get("payload")
        if isinstance(inner, str):
            inner = json.loads(inner)
        response = inner.get("response")
        status = response.get("status")
        mac = response.get("macAddress")
        version = response.get("registerAt")
        if not isinstance(status, dict) or not isinstance(mac, str):
            return None
        if isinstance(version, bool) or not isinstance(version, (int, float)) or not math.isfinite(version):
            return None
        return mac.lower(), version, numeric_tree(status) or {}
    except (ValueError, AttributeError, TypeError, RecursionError):
        return None
