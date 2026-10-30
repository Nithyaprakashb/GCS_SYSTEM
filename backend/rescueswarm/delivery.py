"""Delivery state machine and release transaction bookkeeping."""

from __future__ import annotations

import threading
from typing import Any

from .config import Config
from .db import Database
from .events import EventBus
from .ledger import KitLedger, LedgerError
from .safety import SafetyEngine
from .survivors import SurvivorRegistry
from .telemetry import TelemetryManager, MAV_CMD_DO_SET_SERVO
from .util import iso_now

STATES = [
    "QUEUED", "LAUNCHING", "TRANSIT", "HOLD_CLEARANCE", "DESCENT",
    "HOVER_VERIFY", "RELEASE_SENT", "CONFIRMED", "UNKNOWN", "CLIMB", "RETURNING",
    "LANDING", "STANDBY", "ABORTED",
]

ALLOWED: dict[str, set[str]] = {
    "QUEUED": {"LAUNCHING", "TRANSIT", "ABORTED"},
    "LAUNCHING": {"TRANSIT", "ABORTED", "RETURNING"},
    "TRANSIT": {"HOLD_CLEARANCE", "DESCENT", "ABORTED", "RETURNING"},
    "HOLD_CLEARANCE": {"DESCENT", "ABORTED", "RETURNING"},
    "DESCENT": {"HOVER_VERIFY", "ABORTED", "RETURNING"},
    "HOVER_VERIFY": {"RELEASE_SENT", "ABORTED", "RETURNING"},
    "RELEASE_SENT": {"CONFIRMED", "UNKNOWN"},
    "CONFIRMED": {"CLIMB"},
    "UNKNOWN": {"CLIMB"},
    "CLIMB": {"TRANSIT", "RETURNING"},
    "RETURNING": {"LANDING", "STANDBY"},
    "LANDING": {"STANDBY"},
    "STANDBY": set(),
    "ABORTED": {"RETURNING", "STANDBY"},
}

# Operator-facing assignment lifecycle. Delivery state names are internal
# machine states (CLIMB, STANDBY, …) and must not leak into the allocation
# badge; once a payload is CONFIRMED the assignment stays DELIVERED even
# while the aircraft climbs, returns and parks.
ASSIGNMENT_STATUS = {
    "QUEUED": "ASSIGNED",
    "LAUNCHING": "IN_DELIVERY",
    "TRANSIT": "IN_DELIVERY",
    "HOLD_CLEARANCE": "IN_DELIVERY",
    "DESCENT": "IN_DELIVERY",
    "HOVER_VERIFY": "IN_DELIVERY",
    "RELEASE_SENT": "IN_DELIVERY",
    "CONFIRMED": "DELIVERED",
    "UNKNOWN": "UNKNOWN",
    "ABORTED": "ABORTED",
}

SURVIVOR_STATUS = {
    "QUEUED": "ASSIGNED",
    "LAUNCHING": "IN_DELIVERY",
    "TRANSIT": "IN_DELIVERY",
    "HOLD_CLEARANCE": "IN_DELIVERY",
    "DESCENT": "IN_DELIVERY",
    "HOVER_VERIFY": "IN_DELIVERY",
    "RELEASE_SENT": "IN_DELIVERY",
    "CONFIRMED": "DELIVERED",
    "UNKNOWN": "UNKNOWN",
    "ABORTED": "UNSERVED",
}


class DeliveryEngine:
    def __init__(
        self,
        config: Config,
        db: Database,
        bus: EventBus,
        ledger: KitLedger,
        survivors: SurvivorRegistry,
        safety: SafetyEngine,
        telemetry: TelemetryManager,
    ) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.ledger = ledger
        self.survivors = survivors
        self.safety = safety
        self.telemetry = telemetry
        self._lock = threading.RLock()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.aoi: dict[str, Any] | None = None
        self.allocator: Any = None

    def bind_aoi(self, aoi: dict[str, Any] | None) -> None:
        self.aoi = aoi

    def reset(self) -> None:
        with self._lock:
            self.tasks.clear()

    def create(self, assignment: dict[str, Any]) -> dict[str, Any]:
        task = {
            "assignment_id": assignment["assignment_id"],
            "survivor_id": assignment["survivor_id"],
            "callsign": assignment["callsign"],
            "kit_id": assignment["kit_id"],
            "state": "QUEUED",
            "created_at": iso_now(),
            "updated_at": iso_now(),
            "release_tx_id": None,
            "beam_result": None,
            "clearance": None,
            "history": [{"state": "QUEUED", "at": iso_now(), "detail": "Assignment created"}],
            "eta_s": assignment.get("eta_s"),
        }
        with self._lock:
            self.tasks[assignment["assignment_id"]] = task
        self.bus.publish(
            "delivery.state",
            {"task": dict(task)},
            source=assignment["callsign"],
            data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
        )
        return dict(task)

    def get(self, assignment_id: str) -> dict[str, Any] | None:
        task = self.tasks.get(assignment_id)
        return dict(task) if task else None

    def for_survivor(self, survivor_id: str) -> dict[str, Any] | None:
        for task in self.tasks.values():
            if task["survivor_id"] == survivor_id:
                return dict(task)
        return None

    def queue_for(self, callsign: str) -> list[dict[str, Any]]:
        terminal = {"CONFIRMED", "UNKNOWN", "STANDBY", "ABORTED"}
        return [
            dict(task) for task in self.tasks.values()
            if task["callsign"] == callsign and task["state"] not in terminal
        ]

    def transition(
        self,
        assignment_id: str,
        state: str,
        *,
        detail: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        with self._lock:
            task = self.tasks[assignment_id]
            current = task["state"]
            if state == current:
                return dict(task)
            if state not in ALLOWED.get(current, set()):
                raise ValueError(f"Illegal delivery transition {current} → {state}")
            task["state"] = state
            task["updated_at"] = iso_now()
            task["history"].append({"state": state, "at": iso_now(), "detail": detail})
            for key, value in extra.items():
                task[key] = value
            assignment_status = ASSIGNMENT_STATUS.get(state)
            if assignment_status is not None:
                self.db.update_assignment_status(task["assignment_id"], assignment_status, iso_now())
                if self.allocator is not None:
                    # Keep the allocation view in step with the delivery state
                    # machine; the assignment status is the operator-facing badge.
                    try:
                        self.allocator.set_status(task["assignment_id"], assignment_status)
                    except KeyError:
                        pass
            survivor_status = SURVIVOR_STATUS.get(state)
            if survivor_status:
                try:
                    self.survivors.set_status(
                        task["survivor_id"],
                        survivor_status,
                        owner=task["callsign"],
                        kit_id=task["kit_id"],
                        note=detail or None,
                    )
                except KeyError:
                    pass
        self.bus.publish(
            "delivery.state",
            {"assignment_id": assignment_id, "survivor_id": task["survivor_id"],
             "callsign": task["callsign"], "state": state, "previous": current, "detail": detail},
            severity="warning" if state == "UNKNOWN" else "info",
            source=task["callsign"],
            data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
        )
        return dict(task)

    def request_release(
        self,
        assignment_id: str,
        *,
        hover_verified: bool,
        servo_channel: int = 9,
        release_pwm: int = 1900,
    ) -> dict[str, Any]:
        task = self.tasks[assignment_id]
        survivor = self.survivors.get(task["survivor_id"])
        if not survivor:
            return {"sent": False, "reason": "Survivor missing"}
        clearance = self.safety.release_clearance(
            task["callsign"],
            task["survivor_id"],
            task["kit_id"],
            survivor["drop_lat"],
            survivor["drop_lon"],
            self.telemetry.vehicles,
            self.aoi,
            hover_verified=hover_verified,
        )
        task["clearance"] = clearance
        if not clearance["allowed"]:
            self.bus.publish(
                "payload.denied",
                {"assignment_id": assignment_id, "reasons": clearance["reasons"]},
                severity="warning",
                source="SAFETY",
                data_quality=clearance["measurement_status"],
            )
            return {"sent": False, "reason": "; ".join(clearance["reasons"]), "clearance": clearance}

        kit_ok, kit_reason = self.ledger.release_allowed(task["kit_id"], task["survivor_id"])
        if not kit_ok:
            return {"sent": False, "reason": kit_reason, "clearance": clearance}

        tx = self.telemetry.send_command(
            task["callsign"],
            MAV_CMD_DO_SET_SERVO,
            [float(servo_channel), float(release_pwm), 0, 0, 0, 0, 0],
            f"Release {task['kit_id']} for {task['survivor_id']}",
        )
        self.ledger.mark_release_sent(task["kit_id"], tx.tx_id)
        self.transition(assignment_id, "RELEASE_SENT", detail=f"Release command {tx.tx_id}",
                        release_tx_id=tx.tx_id)
        return {"sent": True, "transaction": tx.to_dict(), "clearance": clearance}

    def apply_beam_result(self, assignment_id: str, beam_result: str, *, event_id: str | None = None) -> dict[str, Any]:
        task = self.tasks[assignment_id]
        if task["state"] != "RELEASE_SENT":
            return {"accepted": False, "reason": f"Task is {task['state']}, not RELEASE_SENT"}
        kit = self.ledger.apply_beam_result(task["kit_id"], beam_result, event_id=event_id)
        confirmed = kit["final_status"] == "DELIVERED"
        self.transition(
            assignment_id,
            "CONFIRMED" if confirmed else "UNKNOWN",
            detail=f"Break-beam result: {beam_result}",
            beam_result=beam_result,
        )
        self.db.record_payload_event(
            {
                "mission_id": self.bus.mission_id,
                "event_id": event_id,
                "kit_id": task["kit_id"],
                "survivor_id": task["survivor_id"],
                "callsign": task["callsign"],
                "command": "RELEASE",
                "beam_result": beam_result,
                "result": kit["final_status"],
                "timestamp": iso_now(),
                "raw": {"assignment_id": assignment_id},
            }
        )
        return {"accepted": True, "confirmed": confirmed, "kit": kit}

    def snapshot(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for task in self.tasks.values():
            counts[task["state"]] = counts.get(task["state"], 0) + 1
        return {
            "tasks": [dict(task) for task in self.tasks.values()],
            "counts": counts,
            "queues": {c: [t["survivor_id"] for t in self.queue_for(c)] for c in self.config.callsigns},
            "policy": "No automatic retry after UNKNOWN. Release requires deterministic clearance.",
        }

