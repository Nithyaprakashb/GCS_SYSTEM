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
    "QUEUED", "LAUNCHING", "HOLD_CLEARANCE", "TRANSIT", "DESCENT",
    "HOVER_VERIFY", "ACK_PENDING", "RELEASE_SENT", "CONFIRMED", "UNKNOWN",
    "CLIMB", "RETURNING", "LANDING", "STANDBY", "ABORTED",
]

# The release path is deliberately narrow: a servo command may only leave the
# GCS once the machine has reached HOVER_VERIFY, and the kit is only marked as
# away until the aircraft has ACKed that specific command.
ALLOWED: dict[str, set[str]] = {
    "QUEUED": {"LAUNCHING", "ABORTED"},
    "LAUNCHING": {"HOLD_CLEARANCE", "TRANSIT", "ABORTED", "RETURNING"},
    "HOLD_CLEARANCE": {"TRANSIT", "ABORTED", "RETURNING"},
    "TRANSIT": {"DESCENT", "ABORTED", "RETURNING"},
    "DESCENT": {"HOVER_VERIFY", "ABORTED", "RETURNING"},
    "HOVER_VERIFY": {"ACK_PENDING", "ABORTED", "RETURNING"},
    # An aborted sortie can strand a release command mid-flight; that kit has left
    # the magazine with no evidence, so UNKNOWN is its honest terminal state.
    "ACK_PENDING": {"RELEASE_SENT", "UNKNOWN", "ABORTED", "RETURNING"},
    "RELEASE_SENT": {"CONFIRMED", "UNKNOWN"},
    "CONFIRMED": {"CLIMB"},
    "UNKNOWN": {"CLIMB"},
    "CLIMB": {"TRANSIT", "RETURNING"},
    "RETURNING": {"LANDING", "STANDBY"},
    "LANDING": {"STANDBY"},
    "STANDBY": set(),
    "ABORTED": {"RETURNING", "STANDBY"},
}

# States in which a release command may be issued. Nothing else may fire a servo.
RELEASE_ELIGIBLE = {"HOVER_VERIFY"}

# Operator-facing assignment lifecycle. Delivery state names are internal
# machine states (CLIMB, STANDBY, …) and must not leak into the allocation
# badge; once a payload is CONFIRMED the assignment stays DELIVERED even
# while the aircraft climbs, returns and parks.
ASSIGNMENT_STATUS = {
    "QUEUED": "ASSIGNED",
    "LAUNCHING": "IN_DELIVERY",
    "HOLD_CLEARANCE": "IN_DELIVERY",
    "TRANSIT": "IN_DELIVERY",
    "DESCENT": "IN_DELIVERY",
    "HOVER_VERIFY": "IN_DELIVERY",
    "ACK_PENDING": "IN_DELIVERY",
    "RELEASE_SENT": "IN_DELIVERY",
    "CONFIRMED": "DELIVERED",
    "UNKNOWN": "UNKNOWN",
    "ABORTED": "ABORTED",
}

SURVIVOR_STATUS = {
    "QUEUED": "ASSIGNED",
    "LAUNCHING": "IN_DELIVERY",
    "HOLD_CLEARANCE": "IN_DELIVERY",
    "TRANSIT": "IN_DELIVERY",
    "DESCENT": "IN_DELIVERY",
    "HOVER_VERIFY": "IN_DELIVERY",
    "ACK_PENDING": "IN_DELIVERY",
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
            "pending_tx_id": None,
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
        servo_channel: int | None = None,
        release_pwm: int | None = None,
    ) -> dict[str, Any]:
        """Command a kit release. The servo frame only leaves after every gate below.

        Ordering matters: the delivery machine must already be in a release-
        eligible state, safety clearance must pass, the kit must be reserved for
        this survivor, and only then is the command sent. The kit is not marked
        away until the aircraft has ACKed that exact command.
        """
        task = self.tasks[assignment_id]
        if task["state"] not in RELEASE_ELIGIBLE:
            self.bus.publish(
                "payload.denied",
                {"assignment_id": assignment_id, "reasons": [f"Task is {task['state']}, not release-eligible"]},
                severity="critical",
                source="DELIVERY",
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
            return {
                "sent": False,
                "reason": f"Release refused: task is {task['state']}, must be one of "
                          + "/".join(sorted(RELEASE_ELIGIBLE)),
            }
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

        # Per-aircraft servo wiring comes from the fleet profile; a shared
        # hard-coded channel would fire the wrong latch on the other magazine.
        channel = servo_channel if servo_channel is not None else int(
            self.config.vehicle(task["callsign"]).get("release_servo_channel", 9)
        )
        pwm = release_pwm if release_pwm is not None else int(
            self.config.vehicle(task["callsign"]).get("release_pwm", 1900)
        )

        # The machine moves to ACK_PENDING *before* the frame is emitted, so a
        # concurrent release on the same task is blocked by the state table.
        self.transition(assignment_id, "ACK_PENDING", detail=f"Release command queued for {task['kit_id']}")
        try:
            tx = self.telemetry.send_command(
                task["callsign"],
                MAV_CMD_DO_SET_SERVO,
                [float(channel), float(pwm), 0, 0, 0, 0, 0],
                f"Release {task['kit_id']} for {task['survivor_id']}",
            )
        except Exception as exc:
            # The command never left the GCS. The kit is UNKNOWN (no evidence), never
            # silently re-queued and never reported as delivered.
            self.ledger.note_command_abandoned(task["kit_id"], reason=f"Release command failed: {exc}")
            self.transition(assignment_id, "UNKNOWN",
                            detail=f"Release command error: {exc}", beam_result="NO_EVIDENCE")
            return {"sent": False, "settled": True, "state": "UNKNOWN", "reason": str(exc)}
        with self._lock:
            self.tasks[assignment_id]["pending_tx_id"] = tx.tx_id
        result = self.settle_release_transaction(tx.tx_id)
        return {"transaction": tx.to_dict(), "clearance": clearance, **result}

    def settle_release_transaction(self, tx_id: str) -> dict[str, Any]:
        """Advance a task on the ACK of its own release command.

        ACKNOWLEDGED -> RELEASE_SENT and the kit is marked away.
        REJECTED / TIMEOUT -> the command is abandoned, the kit is UNKNOWN and is
        never re-sent automatically. SENT (no ACK yet) leaves the task in
        ACK_PENDING so a late ACK can still settle it.
        """
        with self._lock:
            tx = self.telemetry.transaction(tx_id)
            if not tx:
                return {"settled": False, "reason": "Release transaction not found"}
            assignment_id = next(
                (aid for aid, task in self.tasks.items() if task.get("pending_tx_id") == tx_id),
                None,
            )
            if assignment_id is None:
                return {"settled": False, "reason": "No task is awaiting this release transaction"}
            task = self.tasks[assignment_id]
            # The ACK may already have arrived on the reader thread before the caller
            # polled. Settling twice must be a no-op, never a second ledger write.
            if task["state"] != "ACK_PENDING":
                delivered = task["state"] in {"RELEASE_SENT", "CONFIRMED", "CLIMB", "RETURNING", "STANDBY"}
                return {"sent": delivered, "settled": True, "state": task["state"],
                        "already_settled": True}
            state = tx["state"]
            if state == "SENT":
                return {"settled": False, "reason": "Awaiting COMMAND_ACK", "state": "ACK_PENDING"}
            if state in {"ACKNOWLEDGED", "VERIFIED"}:
                self.ledger.mark_release_sent(task["kit_id"], tx_id)
                self.transition(assignment_id, "RELEASE_SENT",
                                detail=f"Release acknowledged ({tx_id})", release_tx_id=tx_id)
                return {"sent": True, "settled": True, "state": "RELEASE_SENT"}
            # Anything else (REJECTED / TIMEOUT / NOT_ADDRESSED / ACK_UNVERIFIED): the command
            # never completed. Distinguish "the kit is still aboard" from "the kit is gone".
            if tx.get("transmitted"):
                # A frame was written to the link and its outcome is unproven. The latch may
                # have fired, so the kit cannot be counted back into the magazine: it becomes
                # UNKNOWN and is never re-fired, and one physical kit is never promised to two
                # survivors.
                self.ledger.note_command_abandoned(
                    task["kit_id"], reason=f"Release command {state}: {tx.get('detail', '')}")
                self.transition(assignment_id, "UNKNOWN",
                                detail=f"Release command {state}: frame transmitted, result unproven",
                                beam_result="NO_EVIDENCE")
                return {"sent": False, "settled": True, "state": "UNKNOWN",
                        "kit_transmitted_unproven": True, "reason": tx.get("detail", state)}
            kit_now = self.ledger.kits.get(task["kit_id"])
            status = (kit_now or {}).get("load_status")
            if status == "RESERVED":
                # The servo frame was never accepted, so the kit is physically still in
                # the magazine. Put it back and mark this attempt aborted: the survivor is
                # unserved and can be re-queued. This is not a lost kit.
                self.ledger.cancel_reservation(task["kit_id"],
                                               reason=f"Release command {state}: {tx.get('detail', '')}")
                self.transition(assignment_id, "ABORTED",
                                detail=f"Release command {state}; kit returned to the magazine")
                return {"sent": False, "settled": True, "state": "ABORTED",
                        "kit_back_in_magazine": True, "reason": tx.get("detail", state)}
            if status == "RELEASE_SENT":
                # A command was acknowledged and then the evidence never arrived: the kit's
                # fate is unknown and it is never re-fired automatically.
                self.ledger.note_command_abandoned(task["kit_id"], reason=f"Release command {state}")
            self.transition(assignment_id, "UNKNOWN",
                            detail=f"Release command {state}: {tx.get('detail', '')}",
                            beam_result="NO_EVIDENCE")
            return {"sent": False, "settled": True, "state": "UNKNOWN", "reason": tx.get("detail", state)}

    def apply_beam_result(self, assignment_id: str, beam_result: str, *, event_id: str | None = None) -> dict[str, Any]:
        task = self.tasks[assignment_id]
        if task["state"] != "RELEASE_SENT":
            return {"accepted": False, "reason": f"Task is {task['state']}, not RELEASE_SENT"}
        if task.get("release_tx_id") is None:
            return {"accepted": False, "reason": "No release command id on this task"}
        # The ledger is the single writer of payload evidence; it enforces the
        # RELEASE_SENT precondition and one-record-per-event-id idempotency.
        kit = self.ledger.apply_beam_result(task["kit_id"], beam_result, event_id=event_id)
        confirmed = kit["final_status"] == "DELIVERED"
        self.transition(
            assignment_id,
            "CONFIRMED" if confirmed else "UNKNOWN",
            detail=f"Break-beam result: {beam_result}",
            beam_result=beam_result,
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

