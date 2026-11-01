"""Configuration loading and persistence for RescueSwarm GCS.

The fleet profile is a versioned file. Operator-editable settings (ports, device
bindings, thresholds) are persisted separately under the local data directory and
deep-merged over the profile so a deployment can be commissioned without editing
source-controlled configuration.
"""

from __future__ import annotations

import copy
import json
import math
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


# ------------------------------------------------------------------ validation
# Every operator-writable setting is declared here. Values outside the declared
# domain are rejected, so a configuration call can never silently dissolve a
# safety threshold. Ranges carry the evidence label from the fleet profile: the
# upper bounds are engineering limits, not flight-verified numbers.
ConfigRule = dict[str, Any]

SAFETY_CRITICAL = "safety_critical"
COMMISSIONED = "commissioned"

CONFIG_SCHEMA: dict[str, dict[str, ConfigRule]] = {
    "telemetry": {
        "topology": {"kind": "enum", "values": ("shared", "independent"), SAFETY_CRITICAL: True},
        # Set only by the physical commissioning procedure in
        # docs/HARDWARE_CONFIG.md, never by an API call: a serial port that
        # enumerates is not a validated aircraft link.
        "topology_verified": {"kind": "protected", "reason": (
            "RFD868x topology verification is a physical commissioning record. "
            "Edit config/fleet.json after bench verification of every link."
        ), SAFETY_CRITICAL: True},
        "gcs_sysid": {"kind": "int", "range": (1, 255)},
        "baud": {"kind": "enum", "values": (57600, 115200, 230400)},
        "shared_endpoint": {"kind": "endpoint"},
        "independent_endpoints": {"kind": "endpoint_map"},
        "heartbeat_timeout_s": {"kind": "num", "range": (1.0, 30.0), SAFETY_CRITICAL: True},
        "stale_after_s": {"kind": "num", "range": (0.5, 30.0), SAFETY_CRITICAL: True},
        "command_timeout_s": {"kind": "num", "range": (0.5, 60.0), SAFETY_CRITICAL: True},
        # How long after an accepted COMMAND_ACK the vehicle is given to show the expected
        # physical state before the transaction is reported ACK_UNVERIFIED.
        "state_verify_s": {"kind": "num", "range": (1.0, 120.0), SAFETY_CRITICAL: True},
        # Mission-protocol waits. Shortening them below the radio round-trip turns a
        # healthy link into a stream of "UNVERIFIED" receipts; lengthening them holds the
        # upload thread on a vehicle that may never answer.
        "upload_timeout_s": {"kind": "num", "range": (2.0, 120.0), SAFETY_CRITICAL: True},
        "readback_timeout_s": {"kind": "num", "range": (2.0, 120.0), SAFETY_CRITICAL: True},
        "param_timeout_s": {"kind": "num", "range": (1.0, 120.0), SAFETY_CRITICAL: True},
        "radio_region": {"kind": "protected", "reason": "Regulatory setting; fixed in the fleet profile."},
        "radio_firmware": {"kind": "text", "max_len": 40},
        "radio_hardware_revision": {"kind": "text", "max_len": 40},
    },
    "mission": {
        # Descent clearance is the vertical separation the release gate demands.
        # 1.5 m is the lowest value that still keeps a 3 m drop inside tolerance.
        "descent_clearance_m": {"kind": "num", "range": (1.5, 100.0), SAFETY_CRITICAL: True},
        "merge_distance_m": {"kind": "num", "range": (1.0, 50.0), SAFETY_CRITICAL: True},
        "time_cap_min": {"kind": "num", "range": (1.0, 30.0), SAFETY_CRITICAL: True},
        "min_battery_to_launch_pct": {"kind": "num", "range": (25.0, 100.0), SAFETY_CRITICAL: True},
        "min_battery_reserve_pct": {"kind": "num", "range": (20.0, 100.0), SAFETY_CRITICAL: True},
        "max_survivors": {"kind": "int", "range": (1, 50), SAFETY_CRITICAL: True},
        "separation_min_m": {"kind": "num", "range": (3.0, 500.0), SAFETY_CRITICAL: True},
        "lane_overlap_fraction": {"kind": "num", "range": (0.0, 0.9)},
        "hover_verify_s": {"kind": "num", "range": (1.0, 120.0), SAFETY_CRITICAL: True},
        "pad_name": {"kind": "text", "max_len": 48},
        "pad_lat": {"kind": "lat"},
        "pad_lon": {"kind": "lon"},
    },
    "video": {
        "required_for_readiness": {"kind": "bool", SAFETY_CRITICAL: True},
        "record_by_default": {"kind": "bool"},
        "freeze_timeout_s": {"kind": "num", "range": (0.5, 60.0)},
        "max_frame_age_s": {"kind": "num", "range": (0.5, 60.0)},
        # How long a started capture pipeline may take to produce its very first frame
        # before the feed is reported as dead on arrival. Declared here so it is a checked
        # setting rather than a magic number buried in the health loop.
        "no_frame_timeout_s": {"kind": "num", "range": (2.0, 120.0)},
        "sim_fps": {"kind": "num", "range": (1.0, 30.0)},
        "black_luma_threshold": {"kind": "num", "range": (0.0, 100.0)},
    },
    "copilot": {
        "enabled": {"kind": "bool"},
        "advisory_only": {"kind": "protected", "reason": (
            "The copilot is advisory-only by design and must never command flight or payload."
        ), SAFETY_CRITICAL: True},
        "max_context_events": {"kind": "int", "range": (10, 500)},
    },
    "simulation": {
        "speed": {"kind": "num", "range": (1.0, 60.0)},
        "tick_hz": {"kind": "num", "range": (1.0, 60.0)},
    },
}


class ConfigError(ValueError):
    """A settings patch was refused."""


def _normalise(rule: ConfigRule, path: str, value: Any) -> Any:
    kind = rule["kind"]
    if kind == "protected":
        raise ConfigError(f"{path} is not operator-settable: {rule.get('reason', 'protected')}")
    if kind == "loose_number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path} must be a number")
        if not math.isfinite(float(value)) or abs(float(value)) > 1_000_000_000:
            raise ConfigError(f"{path} must be a finite, plausible number")
        return value
    if kind == "loose_map":
        if not isinstance(value, dict):
            raise ConfigError(f"{path} must be an object")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 64:
                raise ConfigError(f"{path} has an invalid key")
            if isinstance(item, bool) or not isinstance(item, (int, float, str)) and item is not None:
                raise ConfigError(f"{path}[{key}] must be a simple value")
            if isinstance(item, str) and (len(item) > 240 or any(c in item for c in "\r\n\x00")):
                raise ConfigError(f"{path}[{key}] must be a short text value")
        return copy.deepcopy(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{path} must be true or false")
        return value
    if kind == "enum":
        if value not in rule["values"]:
            raise ConfigError(f"{path} must be one of {', '.join(map(str, rule['values']))}")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{path} must be a whole number")
        low, high = rule["range"]
        if not low <= value <= high:
            raise ConfigError(f"{path} must be between {low} and {high}")
        return value
    if kind == "num":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path} must be a number")
        low, high = rule["range"]
        if not low <= float(value) <= high:
            raise ConfigError(f"{path} must be between {low} and {high}")
        return value
    if kind == "text":
        if not isinstance(value, str) or len(value) > rule.get("max_len", 120) or not value.strip():
            raise ConfigError(f"{path} must be a short text value")
        return value.strip()
    if kind in {"lat", "lon"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path} must be a decimal coordinate")
        limit = 90.0 if kind == "lat" else 180.0
        if not -limit <= float(value) <= limit:
            raise ConfigError(f"{path} must be between -{limit} and {limit}")
        return float(value)
    if kind == "endpoint":
        if value is None or value == "":
            return None
        if not isinstance(value, str) or len(value) > 160 or any(c in value for c in "\r\n\x00"):
            raise ConfigError(f"{path} must be a single serial or network endpoint")
        return value.strip()
    if kind == "endpoint_map":
        if not isinstance(value, dict):
            raise ConfigError(f"{path} must be a callsign-to-endpoint map")
        return {str(k): _normalise({"kind": "endpoint"}, f"{path}[{k}]", v) for k, v in value.items()}
    raise ConfigError(f"{path} has no validation rule")


# Key-name suffixes that always mark a physical commissioning record or a
# regulatory attribute, declared in the schema or not.
PROTECTED_SUFFIXES = ("_verified", "_region", "_hardware_revision")


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


_MISSING = _Missing()


def _rule_for(section: str, key: str, rules: dict[str, ConfigRule],
              current: dict[str, Any] | None) -> ConfigRule:
    """Resolve the rule for a patched key.

    Declared schema rules are authoritative. Keys the shipped profile already carries
    but the schema has not yet classified stay patchable with a type-inferred rule, so
    adding a settings key to fleet.json does not silently make it unpatchable. Names
    that mark commissioning records or regulatory data stay locked either way.
    """
    declared = rules.get(key)
    if declared is not None:
        return declared
    if key.endswith(PROTECTED_SUFFIXES):
        return {"kind": "protected", "reason": "Commissioning or regulatory record; edit config/fleet.json"}
    current_value = (current or {}).get(section, {}).get(key, _MISSING)
    if current_value is _MISSING:
        raise ConfigError(f"{section}.{key} is not a setting this build reads")
    if isinstance(current_value, bool):
        return {"kind": "bool"}
    if isinstance(current_value, (int, float)):
        return {"kind": "loose_number"}
    if isinstance(current_value, str):
        return {"kind": "text", "max_len": 240}
    if isinstance(current_value, dict):
        return {"kind": "loose_map"}
    raise ConfigError(f"{section}.{key} is structural profile data; edit config/fleet.json to change it")


def validate_patch(patch: Any, current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a normalised patch, or refuse it.

    Sections must be known; keys must either be declared in CONFIG_SCHEMA or already
    present in the loaded profile (validated by inferred type). Nothing new can be
    injected through the API.
    """
    if not isinstance(patch, dict):
        raise ConfigError("Configuration patch must be an object")
    unknown_sections = set(patch) - set(CONFIG_SCHEMA)
    if unknown_sections:
        raise ConfigError(
            f"Unknown configuration sections: {', '.join(sorted(unknown_sections))}"
        )
    clean: dict[str, Any] = {}
    for section, values in patch.items():
        rules = CONFIG_SCHEMA[section]
        if not isinstance(values, dict):
            raise ConfigError(f"{section} must be an object of settings")
        if not values:
            raise ConfigError(f"{section} must name at least one setting")
        clean[section] = {
            key: _normalise(_rule_for(section, key, rules, current), f"{section}.{key}", value)
            for key, value in values.items()
        }
    return clean


def safety_keys(patch: dict[str, Any]) -> list[str]:
    """Settings in this patch that gate flight or payload behaviour.

    Anything declared SAFETY_CRITICAL counts. So does any undeclared key under
    telemetry/mission: those sections drive link and envelope decisions, and an
    unclassified threshold must not quietly escape the "re-run preflight" gate.
    """
    result: list[str] = []
    for section, values in patch.items():
        rules = CONFIG_SCHEMA.get(section, {})
        for key in (values or {}):
            declared = rules.get(key, {}).get(SAFETY_CRITICAL)
            undeclared_flight_setting = section in {"telemetry", "mission"} and key not in rules
            if declared or undeclared_flight_setting:
                result.append(f"{section}.{key}")
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
        # Two aircraft sharing one MAVLink system ID would silently shadow the
        # first entry in sysid_map and make that airframe unreachable.
        seen: dict[int, str] = {}
        for aircraft in self._data.get("fleet", []):
            sysid = int(aircraft["sysid"])
            if sysid in seen:
                raise ValueError(
                    f"Fleet profile assigns MAVLink system id {sysid} to both "
                    f"{seen[sysid]} and {aircraft['callsign']}; every aircraft needs a unique id"
                )
            seen[sysid] = aircraft["callsign"]

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
        """Persist a validated settings patch.

        Every key must exist in CONFIG_SCHEMA, must type-check and must fall
        inside its declared engineering range. Protected keys (physical
        commissioning records, the advisory-only copilot guarantee) are never
        operator-settable over the API.
        """
        clean = validate_patch(patch, self.data)
        self._check_cross_fields(clean)
        with self._lock:
            self._overrides = deep_merge(self._overrides, clean)
            self._settings_path.write_text(
                json.dumps(self._overrides, indent=2), encoding="utf-8"
            )
            self._data = deep_merge(self._base, self._overrides)
            return copy.deepcopy(self._data)

    def _check_cross_fields(self, patch: dict[str, Any]) -> None:
        """Reject patches that leave the effective configuration unsound."""
        merged = deep_merge(self._data, patch)
        mission = merged.get("mission", {})
        merge_distance = float(mission.get("merge_distance_m", 5.0))
        clearance = float(mission.get("descent_clearance_m", 20.0))
        if merge_distance > clearance:
            raise ConfigError(
                f"mission.merge_distance_m ({merge_distance}) cannot exceed "
                f"mission.descent_clearance_m ({clearance}): duplicate survivors would be "
                "merged inside the descent separation envelope"
            )
        battery = float(mission.get("min_battery_to_launch_pct", 45.0))
        reserve = float(mission.get("min_battery_reserve_pct", 30.0))
        if battery < reserve:
            raise ConfigError(
                f"mission.min_battery_to_launch_pct ({battery}) must stay at or above "
                f"mission.min_battery_reserve_pct ({reserve})"
            )
