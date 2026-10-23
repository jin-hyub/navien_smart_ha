"""NR-40D discovery and passive protocol capture; no boiler commands are sent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def numeric_tree(value: Any, depth: int = 0) -> Any:
    """Keep numeric protocol fields, never identifiers or arbitrary strings."""
    if depth > 12:
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


@dataclass
class BoilerDevice:
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> BoilerDevice | None:
        if str(raw.get("serviceCode")) != "100" or raw.get("modelName") != "NR-40D":
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

    def diagnostics(self) -> dict[str, Any]:
        return {
            "model_name": "NR-40D",
            "model_code": self.raw.get("modelCode"),
            "control_enabled": False,
            "feature": self.feature,
        }
