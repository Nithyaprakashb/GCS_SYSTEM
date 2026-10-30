"""Deterministic fleet safety rules.

The safety engine is independent of the UI. It evaluates release clearance,
separation, pad occupancy, boundary proximity and return energy using only
timestamped telemetry. Unknown or stale data is never treated as safe.
"""

from __future__ import annotations

import math
import threading
from typing import Any

from .config import Config
from .events import EventBus
from .geo import haversine_m
from .ledger import KitLedger
from .planner import distance_to_boundary_m
from .util import iso_now


class SafetyEngine:
    def __init__(self, config: Config, bus: EventBus, ledger: KitLedger) -> None:
        self.config = config
        self.bus = bus
        self.ledger = ledger
        self._lock = threading.RLock()
        self.last_warnings: dict[str, str] = {}
        self.recall_active = False

    # --------------------------------------------------------------- release
    def release_clearance(
        self,
        callsign: str,
        survivor_id: str,
        kit_id: str,
        target_lat: float,
        target_lon: float,
        vehicles: dict[str, dict[str, Any]],
        aoi: dict[str, Any] | None,
        *,
        hover_verified: bool,
    ) -> dict[str, Any]:
        state = vehicles[callsign]
        mission = self.config.get("mission", default={})
        clearance = float(mission.get("descent_clearance_m", 20.0))
        drop_height = float(self.config.vehicle(callsign).get("drop_height_m", 3.0))
        checks: list[dict[str, Any]] = []

        def add(check_id: str, ok: bool, measured: Any, limit: Any, detail: str) -> None:
            checks.append({
                "check_id": check_id,
                "ok": bool(ok),
                "measured": measured,
                "limit": limit,
                "detail": detail,
            })

        kit_ok, kit_reason = self.ledger.release_allowed(kit_id, survivor_id)
        add("kit_reservation", kit_ok, kit_id, "RESERVED for survivor", kit_reason or "Kit reserved")

        fresh = bool(state.get("connected")) and not state.get("stale") and state.get("identity_valid")
        add("telemetry_fresh", fresh, state.get("data_quality"), "LIVE/SIMULATED", "Vehicle telemetry must be fresh")
        add("armed", bool(state.get("armed")), state.get("armed"), True, "Aircraft must be armed")
        add("mode_guided", state.get("mode") == "GUIDED", state.get("mode"), "GUIDED", "Release only in GUIDED mode")
        agl = state.get("agl_m")
        add(
            "drop_height",
            agl is not None and abs(float(agl) - drop_height) <= 1.0,
            agl,
            f"{drop_height:.1f} ± 1.0 m AGL",
            "TFmini-S height must be within tolerance",
        )
        speed = state.get("groundspeed_ms")
        add(
            "hover_stable",
            speed is not None and float(speed) <= 0.6 and hover_verified,
            speed,
            "≤ 0.6 m/s and hover-verify complete",
            "Position and height must be stable before release",
        )

        if aoi:
            boundary = distance_to_boundary_m(aoi, target_lat, target_lon)
            add("target_inside_fence", boundary > 0.5, round(boundary, 2), "> 0.5 m inside",
                "Drop point must be inside the inclusion fence")

        nearest: list[dict[str, Any]] = []
        for other_callsign, other in vehicles.items():
            if other_callsign == callsign:
                continue
            if other.get("lat") is None or other.get("lon") is None:
                add("traffic_known", False, None, "fresh 3D position",
                    f"{other_callsign} position unavailable; descent denied")
                continue
            if other.get("stale") or not other.get("connected"):
                add("traffic_fresh", False, f"{other_callsign} stale", "fresh telemetry",
                    f"{other_callsign} telemetry is stale; descent denied")
                continue
            horizontal = haversine_m(other["lat"], other["lon"], target_lat, target_lon)
            vertical = None
            if other.get("alt_rel_m") is not None and state.get("alt_rel_m") is not None:
                vertical = abs(float(other["alt_rel_m"]) - float(state["alt_rel_m"]))
            nearest.append({
                "callsign": other_callsign,
                "horizontal_m": round(horizontal, 1),
                "vertical_m": round(vertical, 1) if vertical is not None else None,
                "alt_rel_m": other.get("alt_rel_m"),
            })
            add(
                f"clearance_{other_callsign}",
                horizontal >= clearance,
                round(horizontal, 1),
                f"≥ {clearance:.0f} m horizontal",
                f"{other_callsign} is {horizontal:.1f} m from the drop point",
            )

        checks.append({
            "check_id": "traffic_summary",
            "ok": all(item["horizontal_m"] >= clearance for item in nearest) if nearest else True,
            "measured": nearest,
            "limit": f"{clearance:.0f} m",
            "detail": "Horizontal separation around the drop point",
        })
        allowed = all(check["ok"] for check in checks)
        reasons = [check["detail"] for check in checks if not check["ok"]]
        result = {
            "allowed": allowed,
            "callsign": callsign,
            "survivor_id": survivor_id,
            "kit_id": kit_id,
            "checks": checks,
            "reasons": reasons,
            "evaluated_at": iso_now(),
            "rule": "RELEASE_CLEARANCE_V1",
            "measurement_status": "SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
        }
        if not allowed:
            self.bus.publish(
                "safety.interlock",
                {"rule_id": "RELEASE_DENIED", **result},
                severity="warning",
                source="SAFETY",
                data_quality=result["measurement_status"],
            )
        return result

    # ------------------------------------------------------------- separation
    def separation_warnings(self, vehicles: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        warning_distance = float(self.config.get("mission", "proximity_warn_m", default=8.0))
        warnings: list[dict[str, Any]] = []
        items = list(vehicles.values())
        for index, first in enumerate(items):
            for second in items[index + 1:]:
                if first.get("lat") is None or second.get("lat") is None:
                    continue
                if first.get("stale") or second.get("stale"):
                    continue
                # Parked, disarmed aircraft are staged together at the pad; a
                # 0 m ground separation is normal and must not raise a
                # mid-air proximity warning.
                first_airborne = bool(first.get("armed")) or float(first.get("agl_m") or 0) > 1.0
                second_airborne = bool(second.get("armed")) or float(second.get("agl_m") or 0) > 1.0
                if not first_airborne and not second_airborne:
                    continue
                horizontal = haversine_m(first["lat"], first["lon"], second["lat"], second["lon"])
                vertical = abs(float(first.get("alt_rel_m") or 0) - float(second.get("alt_rel_m") or 0))
                if horizontal < warning_distance and vertical < 8.0:
                    key = f"{first['callsign']}-{second['callsign']}"
                    warnings.append({
                        "rule_id": "PROXIMITY",
                        "pair": key,
                        "horizontal_m": round(horizontal, 1),
                        "vertical_m": round(vertical, 1),
                        "detail": f"{key} separation {horizontal:.1f} m horizontal / {vertical:.1f} m vertical",
                    })
        return warnings

    # -------------------------------------------------------------------- pad
    def pad_status(self, vehicles: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Pad occupancy for launch/landing interlock.

        Parked, disarmed aircraft are intentionally ignored: three aircraft are
        normally staged on or beside the pad before launch. An occupant is an
        armed aircraft on or directly over the pad. Only one such aircraft may
        take off or land at a time.
        """
        mission = self.config.get("mission", default={})
        pad_lat = float(mission.get("pad_lat"))
        pad_lon = float(mission.get("pad_lon"))
        occupants = []
        for state in vehicles.values():
            if state.get("lat") is None or not state.get("armed"):
                continue
            distance = haversine_m(state["lat"], state["lon"], pad_lat, pad_lon)
            agl = state.get("agl_m") or 0.0
            if distance <= 8.0 and agl < 2.0:
                occupants.append({"callsign": state["callsign"], "distance_m": round(distance, 1), "agl_m": agl})
        return {
            "pad": mission.get("pad_name", "PAD-A"),
            "lat": pad_lat,
            "lon": pad_lon,
            "occupants": occupants,
            "clear": len(occupants) == 0,
            "violation": len(occupants) > 1,
            "rule": "One armed aircraft on or over the pad at a time; parked disarmed aircraft ignored",
        }

    # --------------------------------------------------------------- boundary
    def boundary_status(self, aoi: dict[str, Any] | None, state: dict[str, Any]) -> dict[str, Any]:
        if not aoi or state.get("lat") is None:
            return {"available": False}
        distance = distance_to_boundary_m(aoi, state["lat"], state["lon"])
        warning_margin = float(self.config.get("mission", "boundary_warn_m", default=2.0))
        return {
            "available": True,
            "distance_m": round(distance, 2),
            "warning": distance <= warning_margin,
            "breach": distance <= 0.0,
            "callsign": state["callsign"],
        }

    # ---------------------------------------------------------------- energy
    def return_energy_ok(self, state: dict[str, Any]) -> tuple[bool, str]:
        battery = state.get("battery_pct")
        if battery is None or state.get("lat") is None:
            return False, "Battery or position unavailable"
        mission = self.config.get("mission", default={})
        home_lat = state.get("home_lat") or mission.get("pad_lat")
        home_lon = state.get("home_lon") or mission.get("pad_lon")
        distance = haversine_m(state["lat"], state["lon"], home_lat, home_lon)
        profile = self.config.vehicle(state["callsign"])
        speed = float(profile.get("delivery_speed_ms") or profile.get("search_speed_ms") or 8.0)
        transit_min = distance / speed / 60.0
        climb_min = 0.5
        usage = (transit_min + climb_min) * float(mission.get("battery_delivery_pct_per_min", 2.6))
        reserve = float(mission.get("battery_reserve_pct", 22.0))
        required = usage + reserve
        return float(battery) >= required, (
            f"Estimated return need {required:.1f}% (transit {transit_min:.1f} min + {reserve:.0f}% reserve); "
            f"battery {float(battery):.0f}%"
        )

    # --------------------------------------------------------------- recall
    def set_recall(self, active: bool, reason: str = "") -> None:
        self.recall_active = active
        self.bus.publish(
            "safety.recall",
            {"active": active, "reason": reason, "timestamp": iso_now()},
            severity="critical" if active else "info",
            source="SAFETY",
            data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
        )

    # ------------------------------------------------------------ monitoring
    def evaluate_fleet(self, vehicles: dict[str, dict[str, Any]], aoi: dict[str, Any] | None) -> dict[str, Any]:
        separations = self.separation_warnings(vehicles)
        pad = self.pad_status(vehicles)
        boundaries = {
            callsign: self.boundary_status(aoi, state) for callsign, state in vehicles.items()
        }
        for warning in separations:
            key = warning["pair"]
            if self.last_warnings.get(key) != warning["detail"]:
                self.last_warnings[key] = warning["detail"]
                self.bus.publish("safety.interlock", warning, severity="warning", source="SAFETY",
                                 data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE")
        if pad["violation"]:
            self.bus.publish(
                "safety.interlock",
                {"rule_id": "PAD_OCCUPANCY", "occupants": pad["occupants"],
                 "detail": "More than one aircraft on the pad"},
                severity="critical", source="SAFETY",
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
        return {"separations": separations, "pad": pad, "boundaries": boundaries}
