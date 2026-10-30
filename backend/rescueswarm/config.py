"""Configuration loading and persistence for RescueSwarm GCS.

The fleet profile is a versioned file. Operator-editable settings (ports, device
bindings, thresholds) are persisted separately under the local data directory and
deep-merged over the profile so a deployment can be commissioned without editing
source-controlled configuration.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

APP_VERSION = "2.0.0"
CONFIG_VERSION = "1.0.0"

_PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _PACKAGE_DIR.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "fleet.json"
SAMPLES_DIR = PROJECT_ROOT / "samples"


def data_dir() -> Path:
    override = os.environ.get("RESCUESWARM_DATA")
    path = Path(override) if override else PROJECT_ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def deep_merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_CONFIG_PATH
        with self.path.open("r", encoding="utf-8") as handle:
            self._base: dict[str, Any] = json.load(handle)
        self._lock = threading.RLock()
        self._settings_path = data_dir() / "settings.json"
        self._overrides: dict[str, Any] = {}
        if self._settings_path.exists():
            try:
                self._overrides = json.loads(self._settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._overrides = {}
        self._data = deep_merge(self._base, self._overrides)

    # ------------------------------------------------------------------ access
    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return self._data

    def get(self, *keys: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return copy.deepcopy(node)

    @property
    def fleet(self) -> list[dict[str, Any]]:
        return self.get("fleet", default=[])

    def vehicle(self, callsign: str) -> dict[str, Any]:
        for aircraft in self.fleet:
            if aircraft["callsign"] == callsign:
                return copy.deepcopy(aircraft)
        raise KeyError(f"Unknown callsign: {callsign}")

    def search_vehicle(self) -> dict[str, Any]:
        for aircraft in self.fleet:
            if aircraft["role"] == "SEARCH":
                return copy.deepcopy(aircraft)
        raise KeyError("No SEARCH aircraft configured")

    def delivery_vehicles(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(a) for a in self.fleet if a["role"] == "DELIVERY"]

    @property
    def callsigns(self) -> list[str]:
        return [a["callsign"] for a in self.fleet]

    @property
    def kit_count(self) -> int:
        return sum(int(a.get("kits", 0)) for a in self.fleet)

    @property
    def sysid_map(self) -> dict[int, str]:
        return {int(a["sysid"]): a["callsign"] for a in self.fleet}

    def feed_for(self, callsign: str) -> dict[str, Any] | None:
        for feed in self.get("video", "feeds", default=[]):
            if feed["callsign"] == callsign:
                return copy.deepcopy(feed)
        return None

    def public_summary(self) -> dict[str, Any]:
        mission = self.get("mission", default={})
        return {
            "profile_name": self.get("profile_name"),
            "profile_version": self.get("profile_version"),
            "config_version": CONFIG_VERSION,
            "fleet": [
                {
                    "callsign": a["callsign"],
                    "sysid": a["sysid"],
                    "role": a["role"],
                    "frame": a.get("frame"),
                    "kits": a.get("kits", 0),
                    "search_alt_m": a.get("search_alt_m"),
                    "transit_alt_m": a.get("transit_alt_m"),
                }
                for a in self.fleet
            ],
            "mission": {
                "merge_distance_m": mission.get("merge_distance_m"),
                "descent_clearance_m": mission.get("descent_clearance_m"),
                "time_cap_min": mission.get("time_cap_min"),
                "max_survivors": mission.get("max_survivors"),
                "pad_name": mission.get("pad_name"),
                "pad_lat": mission.get("pad_lat"),
                "pad_lon": mission.get("pad_lon"),
            },
            "telemetry": {
                "topology": self.get("telemetry", "topology"),
                "topology_verified": self.get("telemetry", "topology_verified", default=False),
                "baud": self.get("telemetry", "baud"),
                "radio_region": self.get("telemetry", "radio_region"),
            },
        }

    # ------------------------------------------------------------------ mutate
    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Persist a settings patch. Only known top-level sections are accepted."""
        allowed = {"telemetry", "mission", "video", "copilot", "simulation"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"Unknown configuration sections: {', '.join(sorted(unknown))}")
        with self._lock:
            self._overrides = deep_merge(self._overrides, patch)
            self._settings_path.write_text(
                json.dumps(self._overrides, indent=2), encoding="utf-8"
            )
            self._data = deep_merge(self._base, self._overrides)
            return copy.deepcopy(self._data)
