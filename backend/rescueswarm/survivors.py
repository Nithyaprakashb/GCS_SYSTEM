"""Survivor registry: validation, duplicate merge and lifecycle."""

from __future__ import annotations

import math
import threading
from typing import Any

from .config import Config
from .db import Database
from .events import EventBus
from .geo import haversine_m
from .planner import move_inside_boundary, point_inside_aoi
from .util import iso_now, new_id


class SurvivorRegistry:
    # The registry vocabulary, matched to the delivery engine's SURVIVOR_STATUS map and
    # the operator badge union in desktop/src/lib/types.ts.
    STATUSES = {"CONFIRMED", "ASSIGNED", "IN_DELIVERY", "DELIVERED", "UNKNOWN", "UNSERVED"}
    # CONFIRMED is the only allocation-eligible state. UNSERVED is deliberately not
    # terminal: the allocator retries automatically while the sortie runs.
    LOCKED_STATUSES = STATUSES - {"CONFIRMED"}
    MAX_UNCERTAINTY_M = 50.0

    def __init__(self, config: Config, db: Database, bus: EventBus) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self._lock = threading.RLock()
        self.survivors: dict[str, dict[str, Any]] = {}
        self._next_index = 1
        self.aoi: dict[str, Any] | None = None

    def bind_aoi(self, aoi: dict[str, Any] | None) -> None:
        self.aoi = aoi

    def reset(self) -> None:
        with self._lock:
            self.survivors.clear()
            self._next_index = 1

    def ingest_report(
        self,
        aircraft: str,
        envelope: dict[str, Any],
        *,
        data_quality: str = "LIVE",
    ) -> dict[str, Any] | None:
        """Validate and merge a survivor report.

        The report must already be confirmed by the onboard three-frame logic.
        The GCS re-validates fields and merges anything within the configured
        duplicate distance. A merged report never creates a second kit demand.
        """
        mission_id = self.bus.mission_id
        if not mission_id:
            return None
        try:
            lat = float(envelope.get("lat", envelope.get("latitude_e7", 0) / 1e7))
            lon = float(envelope.get("lon", envelope.get("longitude_e7", 0) / 1e7))
        except (TypeError, ValueError):
            self._reject(aircraft, "Invalid coordinates in survivor report")
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            self._reject(aircraft, "Survivor coordinates outside WGS84 bounds", data_quality)
            return None
        try:
            confidence = float(envelope.get("confidence_mean", envelope.get("confidence", 0.0)))
            frames = int(envelope.get("frames_confirmed", 3))
            uncertainty = float(envelope.get("geotag_uncertainty_m", 3.0))
        except (TypeError, ValueError):
            self._reject(aircraft, "Survivor report carries a non-numeric metric", data_quality)
            return None
        # NaN and ±inf survive a "<" comparison (every NaN test is False), so they must be
        # refused explicitly or they poison the weighted merge and the stored evidence.
        if not all(math.isfinite(value) for value in (confidence, uncertainty)) or frames < 0:
            self._reject(aircraft, "Survivor report metric is not finite", data_quality)
            return None
        if confidence < 0.85 or frames < 3:
            self._reject(
                aircraft,
                f"Confirmation threshold not met (confidence={confidence:.2f}, frames={frames})",
                data_quality,
            )
            return None
        if uncertainty <= 0:
            # A zero or negative radius is not a better fix than the worst one we accept;
            # it is a malformed field, so report it as the cap and keep the warning visible.
            self._reject(
                aircraft,
                f"Impausible geotag uncertainty {uncertainty} m; carried as "
                f"{self.MAX_UNCERTAINTY_M:.0f} m",
                data_quality,
            )
            uncertainty = self.MAX_UNCERTAINTY_M
        elif uncertainty > self.MAX_UNCERTAINTY_M:
            self._reject(
                aircraft,
                f"Impausible geotag uncertainty {uncertainty:.1f} m; carried as "
                f"{self.MAX_UNCERTAINTY_M:.0f} m",
                data_quality,
            )
            uncertainty = self.MAX_UNCERTAINTY_M
        if self.aoi and not point_inside_aoi(self.aoi, lat, lon):
            self._reject(aircraft, "Survivor report lies outside the mission boundary", data_quality)
            return None

        merge_distance = float(self.config.get("mission", "merge_distance_m", default=5.0))
        with self._lock:
            duplicate = None
            duplicate_distance = math.inf
            for survivor in self.survivors.values():
                distance = haversine_m(survivor["lat"], survivor["lon"], lat, lon)
                if distance <= merge_distance and distance < duplicate_distance:
                    duplicate = survivor
                    duplicate_distance = distance
            if duplicate:
                total_weight = duplicate["confidence"] * duplicate["observations"] + confidence
                # A survivor that already has an aircraft committed to it must not drift:
                # the delivery vehicle navigates to the geotag that was assigned, and a
                # merge that moves it would silently re-aim a live drop.
                position_locked = duplicate["status"] != "CONFIRMED"
                if total_weight > 0 and not position_locked:
                    duplicate["lat"] = round((
                        duplicate["lat"] * duplicate["confidence"] * duplicate["observations"] + lat * confidence
                    ) / total_weight, 7)
                    duplicate["lon"] = round((
                        duplicate["lon"] * duplicate["confidence"] * duplicate["observations"] + lon * confidence
                    ) / total_weight, 7)
                duplicate["observations"] += 1
                duplicate["confidence"] = max(duplicate["confidence"], confidence)
                duplicate["uncertainty_m"] = round(
                    max(0.5, min(duplicate["uncertainty_m"], uncertainty)), 2
                )
                duplicate["last_seen"] = iso_now()
                duplicate["updated_at"] = iso_now()
                self.db.upsert_survivor(duplicate)
                self.db.insert_observation(
                    {
                        "observation_id": new_id("OBS"),
                        "mission_id": mission_id,
                        "survivor_id": duplicate["survivor_id"],
                        "aircraft": aircraft,
                        "source_time": envelope.get("source_time") or iso_now(),
                        "frame_count": frames,
                        "confidence": confidence,
                        "model_version": envelope.get("model_version"),
                        "calibration_version": envelope.get("camera_calibration_version"),
                        "lat": lat,
                        "lon": lon,
                        "raw": envelope,
                    }
                )
                self.bus.publish(
                    "survivor.updated",
                    {
                        "survivor": self._public(duplicate),
                        "merged": True,
                        "merge_distance_m": round(duplicate_distance, 2),
                        "geotag_locked": position_locked,
                        "source_aircraft": aircraft,
                    },
                    source=aircraft,
                    data_quality=data_quality,
                )
                return self._public(duplicate)

            cap = int(self.config.get("mission", "max_survivors", default=10))
            if len(self.survivors) >= cap:
                # Every new survivor is a kit demand and a slot in the allocation loop.
                # Without a bound a flooded detector would grow the registry past the
                # magazine capacity and stall the operator's screen.
                self._reject(
                    aircraft,
                    f"Survivor registry is at its configured capacity ({cap}); "
                    f"detection not registered as a new survivor",
                    data_quality,
                )
                return None

            survivor_id = f"S-{self._next_index:02d}"
            self._next_index += 1
            drop_lat, drop_lon = lat, lon
            if self.aoi:
                drop_lat, drop_lon = move_inside_boundary(
                    self.aoi,
                    lat,
                    lon,
                    float(self.config.get("mission", "boundary_drop_inset_m", default=2.0)),
                )
            survivor = {
                "survivor_id": survivor_id,
                "mission_id": mission_id,
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "drop_lat": round(drop_lat, 7),
                "drop_lon": round(drop_lon, 7),
                "uncertainty_m": round(uncertainty, 2),
                "confidence": round(confidence, 3),
                "status": "CONFIRMED",
                "source_aircraft": aircraft,
                "owner": None,
                "kit_id": None,
                "eta_s": None,
                "first_seen": iso_now(),
                "last_seen": iso_now(),
                "updated_at": iso_now(),
                "observations": 1,
                "data_quality": data_quality,
                "notes": "",
            }
            self.survivors[survivor_id] = survivor
            self.db.upsert_survivor(survivor)
            self.db.insert_observation(
                {
                    "observation_id": new_id("OBS"),
                    "mission_id": mission_id,
                    "survivor_id": survivor_id,
                    "aircraft": aircraft,
                    "source_time": envelope.get("source_time") or iso_now(),
                    "frame_count": frames,
                    "confidence": confidence,
                    "model_version": envelope.get("model_version"),
                    "calibration_version": envelope.get("camera_calibration_version"),
                    "lat": lat,
                    "lon": lon,
                    "raw": envelope,
                }
            )
            self.bus.publish(
                "survivor.created",
                {"survivor": self._public(survivor)},
                severity="warning",
                source=aircraft,
                data_quality=data_quality,
            )
            return self._public(survivor)

    def _reject(self, aircraft: str, reason: str, data_quality: str = "LIVE") -> None:
        self.bus.publish(
            "survivor.rejected",
            {"source_aircraft": aircraft, "reason": reason},
            severity="warning",
            source=aircraft,
            data_quality=data_quality,
        )

    def set_status(
        self,
        survivor_id: str,
        status: str,
        *,
        owner: str | None = None,
        kit_id: str | None = None,
        eta_s: float | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if status not in self.STATUSES:
            raise ValueError(f"Unknown survivor status {status!r}")
        with self._lock:
            survivor = self.survivors.get(survivor_id)
            if survivor is None:
                raise KeyError(f"Unknown survivor {survivor_id}")
            previous = survivor["status"]
            if previous == "DELIVERED" and status != "UNKNOWN":
                # A delivered case is durable beam evidence. A late or duplicated message
                # must not turn it back into open demand and re-fire an allocation; only an
                # explicit evidence reconciliation may downgrade it to UNKNOWN.
                raise ValueError(
                    f"Survivor {survivor_id} is DELIVERED; refusing to set it {status}"
                )
            survivor["status"] = status
            if owner is not None:
                survivor["owner"] = owner
            if kit_id is not None:
                survivor["kit_id"] = kit_id
            if eta_s is not None:
                survivor["eta_s"] = round(eta_s, 1)
            if note:
                survivor["notes"] = note
            survivor["updated_at"] = iso_now()
            self.db.upsert_survivor(survivor)
            self.bus.publish(
                "survivor.updated",
                {"survivor": self._public(survivor), "previous_status": previous},
                source="GCS",
                data_quality=survivor.get("data_quality", "LIVE"),
            )
            return self._public(survivor)

    def get(self, survivor_id: str) -> dict[str, Any] | None:
        survivor = self.survivors.get(survivor_id)
        return self._public(survivor) if survivor else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(s) for s in self.survivors.values()]

    def confirmed_unassigned(self) -> list[dict[str, Any]]:
        return [s for s in self.list() if s["status"] == "CONFIRMED"]

    def _public(self, survivor: dict[str, Any]) -> dict[str, Any]:
        return dict(survivor)

    def snapshot(self) -> dict[str, Any]:
        survivors = self.list()
        counts: dict[str, int] = {}
        for survivor in survivors:
            counts[survivor["status"]] = counts.get(survivor["status"], 0) + 1
        return {
            "survivors": survivors,
            "counts": counts,
            "total": len(survivors),
            "merge_distance_m": self.config.get("mission", "merge_distance_m", default=5.0),
            "max_survivors": self.config.get("mission", "max_survivors", default=10),
        }
