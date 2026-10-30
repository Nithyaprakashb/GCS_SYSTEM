"""Deterministic shortest-ETA task allocation.

Allocation is the only authority that assigns a survivor to a delivery aircraft.
Every candidate, rejection reason, ETA input and tie-break is persisted. A kit is
reserved atomically with the assignment, so a survivor can never hold two kits.
"""

from __future__ import annotations

import math
import threading
from typing import Any

from .config import Config
from .db import Database
from .events import EventBus
from .geo import haversine_m
from .ledger import KitLedger, LedgerError
from .survivors import SurvivorRegistry
from .util import iso_now, new_id


class AllocationError(RuntimeError):
    pass


class TaskAllocator:
    def __init__(
        self,
        config: Config,
        db: Database,
        bus: EventBus,
        ledger: KitLedger,
        survivors: SurvivorRegistry,
    ) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.ledger = ledger
        self.survivors = survivors
        self._lock = threading.RLock()
        self.assignments: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def reset(self) -> None:
        with self._lock:
            self.assignments.clear()
            self._counter = 0

    def restore(self, mission_id: str) -> None:
        rows = self.db.get_assignments(mission_id)
        with self._lock:
            self.assignments = {row["assignment_id"]: row for row in rows}
            self._counter = len(rows)

    # --------------------------------------------------------------- queueing
    def queue_for(self, callsign: str) -> list[dict[str, Any]]:
        active = {"ASSIGNED", "IN_DELIVERY", "QUEUED", "LAUNCHING", "TRANSIT", "HOLD_CLEARANCE",
                  "DESCENT", "HOVER_VERIFY", "RELEASE_SENT"}
        result = []
        for assignment in self.assignments.values():
            if assignment["callsign"] != callsign or assignment["status"] not in active:
                continue
            survivor = self.survivors.get(assignment["survivor_id"])
            if survivor:
                result.append(survivor)
        return result

    # -------------------------------------------------------------- allocate
    def allocate(self, survivor_id: str, vehicles: dict[str, dict[str, Any]]) -> dict[str, Any]:
        survivor = self.survivors.get(survivor_id)
        if not survivor:
            raise AllocationError(f"Unknown survivor {survivor_id}")
        if survivor["status"] in {"ASSIGNED", "IN_DELIVERY", "DELIVERED"}:
            existing = self.assignment_for(survivor_id)
            if existing:
                return {"allocated": False, "reason": "Survivor already assigned",
                        "assignment": existing, "survivor": survivor}
        # UNSERVED is retryable: an earlier attempt found no eligible aircraft or
        # free kit. Retrying is safe because a survivor in this state holds no
        # reservation; the ledger reserve remains the single atomic gate.
        if survivor["status"] in {"DUPLICATE", "REJECTED"}:
            return {"allocated": False, "reason": f"Survivor status {survivor['status']}",
                    "survivor": survivor}

        target = (survivor["drop_lat"], survivor["drop_lon"])
        speed = float(self.config.delivery_vehicles()[0]["delivery_speed_ms"])
        service = float(self.config.get("mission", "service_time_s", default=25.0))
        reserve_pct = float(self.config.get("mission", "battery_reserve_pct", default=22.0))
        launch_pct = float(self.config.get("mission", "min_battery_to_launch_pct", default=55.0))

        candidates: list[dict[str, Any]] = []
        rejections: list[dict[str, Any]] = []
        for profile in self.config.delivery_vehicles():
            callsign = profile["callsign"]
            state = vehicles.get(callsign, {})
            queue = self.queue_for(callsign)
            free_kits = self.ledger.free_count(callsign)
            reasons: list[str] = []
            if not state.get("connected"):
                reasons.append("No telemetry link")
            if state.get("stale"):
                reasons.append("Telemetry stale")
            if not state.get("identity_valid"):
                reasons.append("Vehicle identity not validated")
            if state.get("battery_pct") is not None:
                battery = float(state["battery_pct"])
                threshold = launch_pct if not state.get("armed") else reserve_pct + 10.0
                if battery < threshold:
                    reasons.append(f"Battery {battery:.0f}% below {threshold:.0f}% threshold")
            else:
                reasons.append("Battery unknown")
            if free_kits <= 0:
                reasons.append("No free kit")
            if state.get("returning_for_safety"):
                reasons.append("Returning for safety/failsafe")
            if state.get("failsafe"):
                reasons.append(f"Failsafe active: {state['failsafe']}")
            link_quality = state.get("link_quality")
            if link_quality is not None and float(link_quality) < 30:
                reasons.append(f"Link quality {link_quality}% below 30%")
            if state.get("task") in {"RETURNING", "LANDING", "EMERGENCY"}:
                reasons.append(f"Aircraft task is {state['task']}")
            if reasons:
                rejections.append({"callsign": callsign, "reasons": reasons, "free_kits": free_kits})
                continue

            if queue:
                start_lat, start_lon = queue[-1]["drop_lat"], queue[-1]["drop_lon"]
                start_source = f"queue tail {queue[-1]['survivor_id']}"
            elif state.get("lat") is not None:
                start_lat, start_lon = state["lat"], state["lon"]
                start_source = "current position"
            else:
                start_lat = self.config.get("mission", "pad_lat")
                start_lon = self.config.get("mission", "pad_lon")
                start_source = "launch pad"
            distance = haversine_m(start_lat, start_lon, target[0], target[1])
            eta_s = distance / speed + service * len(queue)
            candidates.append(
                {
                    "callsign": callsign,
                    "eta_s": round(eta_s, 1),
                    "eta_min": round(eta_s / 60.0, 2),
                    "distance_m": round(distance, 1),
                    "queue_depth": len(queue),
                    "free_kits": free_kits,
                    "start_source": start_source,
                    "start": {"lat": round(start_lat, 7), "lon": round(start_lon, 7)},
                    "battery_pct": state.get("battery_pct"),
                }
            )

        if not candidates:
            self.survivors.set_status(
                survivor_id, "UNSERVED", note="No eligible delivery aircraft or free kit"
            )
            decision = {
                "allocated": False,
                "reason": "No eligible delivery aircraft",
                "candidates": [],
                "rejections": rejections,
                "survivor": self.survivors.get(survivor_id),
                "decided_at": iso_now(),
                "rule": "ELIGIBILITY_FILTER",
            }
            self.bus.publish(
                "allocation.created", decision, severity="warning", source="ALLOCATOR",
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
            return decision

        # Deterministic: shortest ETA, then more free kits, then aircraft ID.
        ranked = sorted(candidates, key=lambda item: (item["eta_s"], -item["free_kits"], item["callsign"]))
        selected = ranked[0]
        callsign = selected["callsign"]
        try:
            kit = self.ledger.reserve(callsign, survivor_id)
        except LedgerError as exc:
            kit = None
            selected["reasons"] = [str(exc)]
        if not kit:
            # Reservation failed after the eligibility pass: remove this candidate
            # and retry deterministically instead of over-booking.
            remaining = [c for c in ranked if c["callsign"] != callsign]
            if remaining:
                return self._allocate_from(
                    survivor_id, survivor, remaining, rejections, vehicles
                )
            self.survivors.set_status(survivor_id, "UNSERVED", note="Kit reservation failed")
            return {"allocated": False, "reason": "Kit reservation failed", "candidates": ranked,
                    "rejections": rejections, "survivor": self.survivors.get(survivor_id)}

        with self._lock:
            self._counter += 1
            assignment_id = f"ASG-{self._counter:03d}"
            assignment = {
                "assignment_id": assignment_id,
                "mission_id": self.bus.mission_id,
                "survivor_id": survivor_id,
                "callsign": callsign,
                "eta_s": selected["eta_s"],
                "kit_id": kit["kit_id"],
                "status": "ASSIGNED",
                "created_at": iso_now(),
                "updated_at": iso_now(),
                "decision": {
                    "rule": "MIN_ETA_ELIGIBLE",
                    "candidates": ranked,
                    "rejections": rejections,
                    "selected": callsign,
                    "selected_reason": "Lowest ETA among eligible aircraft",
                    "tie_break": "more free kits, then lower callsign",
                    "kit_id": kit["kit_id"],
                    "survivor_position": {"lat": survivor["drop_lat"], "lon": survivor["drop_lon"]},
                    "distance_m": selected["distance_m"],
                    "queue_depth": selected["queue_depth"],
                    "service_time_s": service,
                    "transit_speed_ms": speed,
                },
            }
            self.assignments[assignment_id] = assignment
            self.db.upsert_assignment(assignment)
        self.survivors.set_status(
            survivor_id, "ASSIGNED", owner=callsign, kit_id=kit["kit_id"], eta_s=selected["eta_s"]
        )
        decision = {
            "allocated": True,
            "assignment": assignment,
            "candidates": ranked,
            "rejections": rejections,
            "survivor": self.survivors.get(survivor_id),
            "decided_at": iso_now(),
        }
        self.bus.publish(
            "allocation.created", decision, source="ALLOCATOR",
            data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
        )
        return decision

    def _allocate_from(
        self,
        survivor_id: str,
        survivor: dict[str, Any],
        candidates: list[dict[str, Any]],
        rejections: list[dict[str, Any]],
        vehicles: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Fallback path when the first-choice reservation loses a race."""
        for candidate in candidates:
            callsign = candidate["callsign"]
            try:
                kit = self.ledger.reserve(callsign, survivor_id)
            except LedgerError:
                continue
            if not kit:
                continue
            with self._lock:
                self._counter += 1
                assignment_id = f"ASG-{self._counter:03d}"
                assignment = {
                    "assignment_id": assignment_id,
                    "mission_id": self.bus.mission_id,
                    "survivor_id": survivor_id,
                    "callsign": callsign,
                    "eta_s": candidate["eta_s"],
                    "kit_id": kit["kit_id"],
                    "status": "ASSIGNED",
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                    "decision": {
                        "rule": "MIN_ETA_ELIGIBLE_RESERVATION_FALLBACK",
                        "candidates": candidates,
                        "rejections": rejections,
                        "selected": callsign,
                        "kit_id": kit["kit_id"],
                    },
                }
                self.assignments[assignment_id] = assignment
                self.db.upsert_assignment(assignment)
            self.survivors.set_status(
                survivor_id, "ASSIGNED", owner=callsign, kit_id=kit["kit_id"], eta_s=candidate["eta_s"]
            )
            decision = {"allocated": True, "assignment": assignment, "candidates": candidates,
                        "rejections": rejections, "survivor": self.survivors.get(survivor_id)}
            self.bus.publish(
                "allocation.created", decision, source="ALLOCATOR",
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
            return decision
        self.survivors.set_status(survivor_id, "UNSERVED", note="Kit reservation failed for all candidates")
        return {"allocated": False, "reason": "Kit reservation failed", "candidates": candidates,
                "rejections": rejections, "survivor": self.survivors.get(survivor_id)}

    # ---------------------------------------------------------------- updates
    def assignment_for(self, survivor_id: str) -> dict[str, Any] | None:
        for assignment in self.assignments.values():
            if assignment["survivor_id"] == survivor_id:
                return dict(assignment)
        return None

    def active_for(self, callsign: str) -> list[dict[str, Any]]:
        active = {"ASSIGNED", "IN_DELIVERY", "QUEUED", "LAUNCHING", "TRANSIT", "HOLD_CLEARANCE",
                  "DESCENT", "HOVER_VERIFY", "RELEASE_SENT", "CLIMB"}
        return [
            dict(a) for a in self.assignments.values()
            if a["callsign"] == callsign and a["status"] in active
        ]

    def set_status(self, assignment_id: str, status: str) -> dict[str, Any]:
        assignment = self.assignments[assignment_id]
        assignment["status"] = status
        assignment["updated_at"] = iso_now()
        self.db.upsert_assignment(assignment)
        self.bus.publish(
            "allocation.updated",
            {"assignment_id": assignment_id, "survivor_id": assignment["survivor_id"],
             "callsign": assignment["callsign"], "status": status},
            source="ALLOCATOR",
            data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
        )
        return dict(assignment)

    def snapshot(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for assignment in self.assignments.values():
            counts[assignment["status"]] = counts.get(assignment["status"], 0) + 1
        queues = {c: [s["survivor_id"] for s in self.queue_for(c)] for c in self.config.callsigns}
        return {
            "assignments": [dict(a) for a in self.assignments.values()],
            "counts": counts,
            "queues": queues,
            "rule": "Eligibility filter, then shortest queue-aware ETA; tie-break: more free kits, then lower callsign.",
        }
