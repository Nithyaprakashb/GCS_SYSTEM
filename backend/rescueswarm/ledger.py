"""Transactional shared kit ledger.

There are ten physical kits pooled across the two delivery aircraft. Reservation
is atomic, a kit cannot be reserved twice, and DELIVERED is only possible with
validated break-beam evidence. A missing or ambiguous beam result becomes
UNKNOWN and is never automatically re-fired.
"""

from __future__ import annotations

import threading
from typing import Any

from .config import Config
from .db import Database
from .events import EventBus
from .util import iso_now

FREE_STATES = {"LOADED"}
RESERVED_STATES = {"RESERVED", "RELEASE_SENT"}


class LedgerError(RuntimeError):
    pass


class KitLedger:
    def __init__(self, config: Config, db: Database, bus: EventBus) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self._lock = threading.RLock()
        self.kits: dict[str, dict[str, Any]] = {}
        self.initialised = False
        # Event ids already admitted as release evidence, so a replayed or
        # duplicated break-beam report can never be counted twice.
        self._seen_events: set[str] = set()

    def initialise(self, mission_id: str) -> None:
        with self._lock:
            self.kits.clear()
            for profile in self.config.delivery_vehicles():
                prefix = profile.get("kit_prefix") or f"{profile['callsign']}-K"
                for index in range(1, int(profile.get("kits", 0)) + 1):
                    kit_id = f"{prefix}{index:02d}"
                    kit = {
                        "kit_id": kit_id,
                        "mission_id": mission_id,
                        "callsign": profile["callsign"],
                        "load_status": "LOADED",
                        "reserved_for": None,
                        "release_command_id": None,
                        "final_status": None,
                        "updated_at": iso_now(),
                    }
                    self.kits[kit_id] = kit
                    self.db.upsert_kit(kit)
            self.initialised = True

    def reset(self) -> None:
        with self._lock:
            self.kits.clear()
            self._seen_events.clear()
            self.initialised = False

    def restore_from_db(self, mission_id: str) -> None:
        rows = self.db.get_kits(mission_id)
        if rows:
            with self._lock:
                self.kits = {row["kit_id"]: dict(row) for row in rows}
                try:
                    self._seen_events = {
                        str(row["event_id"])
                        for row in self.db.get_payload_events(mission_id)
                        if row.get("event_id")
                    }
                except Exception:
                    self._seen_events = set()
                self.initialised = True

    def cancel_reservation(self, kit_id: str, *, reason: str = "") -> dict[str, Any]:
        """Return an un-flown reserved kit to the pool.

        A kit whose release was never commanded is still physically in the
        magazine, so it becomes available again. Once a release command has left
        the GCS the kit's fate is unknown and it is never silently recycled.
        """
        with self._lock:
            kit = self.kits[kit_id]
            if kit["load_status"] != "RESERVED":
                return dict(kit)
            survivor = kit["reserved_for"]
            kit["load_status"] = "LOADED"
            kit["reserved_for"] = None
            kit["release_command_id"] = None
            kit["updated_at"] = iso_now()
            self.db.upsert_kit(kit)
            self.bus.publish(
                "payload.released_back",
                {"kit_id": kit_id, "survivor_id": survivor, "callsign": kit["callsign"],
                 "reason": reason or "reservation cancelled"},
                source="LEDGER",
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
            return dict(kit)

    def note_command_abandoned(self, kit_id: str, *, reason: str) -> dict[str, Any]:
        """A release command was sent but never verified: the kit is lost to this sortie.

        Valid from RESERVED as well as RELEASE_SENT. A reservation means the ground station
        still counts the kit aboard; that claim only holds while no frame went out. Once a
        release command has been emitted and its outcome is unknown, the honest state is
        UNKNOWN — returning the kit to the magazine would let a second survivor be "served"
        with hardware that may already be on the ground.
        """
        with self._lock:
            kit = self.kits[kit_id]
            if kit["load_status"] not in {"RELEASE_SENT", "RESERVED"}:
                return dict(kit)
            kit["load_status"] = "UNKNOWN"
            kit["final_status"] = "UNKNOWN"
            kit["updated_at"] = iso_now()
            self.db.upsert_kit(kit)
            self.bus.publish(
                "payload.result",
                {"kit_id": kit_id, "survivor_id": kit["reserved_for"], "callsign": kit["callsign"],
                 "result": "UNKNOWN", "beam_result": "NO_EVIDENCE", "reason": reason,
                 "auto_retry": False},
                severity="critical",
                source=kit["callsign"],
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
            return dict(kit)

    def free_count(self, callsign: str) -> int:
        return sum(1 for k in self.kits.values() if k["callsign"] == callsign and k["load_status"] == "LOADED")

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for kit in self.kits.values():
            result[kit["load_status"]] = result.get(kit["load_status"], 0) + 1
        return result

    def total(self) -> int:
        return len(self.kits)

    def total_free(self) -> int:
        return sum(1 for kit in self.kits.values() if kit["load_status"] == "LOADED")

    def reserved_for(self, survivor_id: str) -> dict[str, Any] | None:
        for kit in self.kits.values():
            if kit["reserved_for"] == survivor_id:
                return dict(kit)
        return None

    def reserve(self, callsign: str, survivor_id: str) -> dict[str, Any] | None:
        with self._lock:
            existing = self.reserved_for(survivor_id)
            if existing:
                raise LedgerError(f"Survivor {survivor_id} already has kit {existing['kit_id']} reserved")
            candidates = [
                kit for kit in self.kits.values()
                if kit["callsign"] == callsign and kit["load_status"] == "LOADED"
            ]
            if not candidates:
                return None
            kit = sorted(candidates, key=lambda item: item["kit_id"])[0]
            kit["load_status"] = "RESERVED"
            kit["reserved_for"] = survivor_id
            kit["updated_at"] = iso_now()
            self.db.upsert_kit(kit)
            self.bus.publish(
                "payload.reserved",
                {"kit_id": kit["kit_id"], "survivor_id": survivor_id, "callsign": callsign},
                source="LEDGER",
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
            return dict(kit)

    def release_allowed(self, kit_id: str, survivor_id: str) -> tuple[bool, str]:
        kit = self.kits.get(kit_id)
        if not kit:
            return False, "Kit not found"
        if kit["final_status"] in {"DELIVERED", "UNKNOWN"}:
            return False, f"Kit already terminal ({kit['final_status']}); duplicate release blocked"
        if kit["load_status"] != "RESERVED" or kit["reserved_for"] != survivor_id:
            return False, "Kit is not reserved for this survivor"
        return True, ""

    def mark_release_sent(self, kit_id: str, command_id: str) -> dict[str, Any]:
        with self._lock:
            kit = self.kits[kit_id]
            if kit["load_status"] != "RESERVED":
                raise LedgerError(f"Kit {kit_id} cannot be released from state {kit['load_status']}")
            kit["load_status"] = "RELEASE_SENT"
            kit["release_command_id"] = command_id
            kit["updated_at"] = iso_now()
            self.db.upsert_kit(kit)
            self.bus.publish(
                "payload.command",
                {"kit_id": kit_id, "survivor_id": kit["reserved_for"], "command_id": command_id,
                 "callsign": kit["callsign"]},
                source="LEDGER",
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
            return dict(kit)

    def apply_beam_result(self, kit_id: str, beam_result: str, *, event_id: str | None = None) -> dict[str, Any]:
        """Resolve a release transaction from break-beam evidence.

        beam_result: CONFIRMED / NO_BEAM / SENSOR_FAULT / UNKNOWN

        Evidence is admissible only for a kit whose release was actually
        commanded, only once per event id, and only with an id that ties it to a
        specific report. A kit still sitting in the magazine cannot be delivered.
        """
        with self._lock:
            kit = self.kits[kit_id]
            if kit["load_status"] != "RELEASE_SENT":
                raise LedgerError(
                    f"Beam result for kit {kit_id} refused: kit is {kit['load_status']}, "
                    "not RELEASE_SENT — no release was commanded, so this cannot be delivery evidence"
                )
            if kit["final_status"] in {"DELIVERED", "UNKNOWN"}:
                return dict(kit)
            if not event_id:
                raise LedgerError(f"Beam result for kit {kit_id} refused: no event id, evidence is unattributable")
            if event_id in self._seen_events:
                raise LedgerError(f"Beam event {event_id} refused: duplicate of evidence already recorded")
            self._seen_events.add(event_id)
            if beam_result == "CONFIRMED":
                kit["load_status"] = "DELIVERED"
                kit["final_status"] = "DELIVERED"
            else:
                kit["load_status"] = "UNKNOWN"
                kit["final_status"] = "UNKNOWN"
            kit["updated_at"] = iso_now()
            self.db.upsert_kit(kit)
            self.db.record_payload_event(
                {
                    "mission_id": kit["mission_id"],
                    "event_id": event_id,
                    "kit_id": kit_id,
                    "survivor_id": kit["reserved_for"],
                    "callsign": kit["callsign"],
                    "command": "RELEASE",
                    "beam_result": beam_result,
                    "result": kit["final_status"],
                    "timestamp": iso_now(),
                    "raw": {"beam_result": beam_result},
                }
            )
            self.bus.publish(
                "payload.result",
                {
                    "kit_id": kit_id,
                    "survivor_id": kit["reserved_for"],
                    "callsign": kit["callsign"],
                    "result": kit["final_status"],
                    "beam_result": beam_result,
                    "auto_retry": False,
                },
                severity="warning" if kit["final_status"] == "UNKNOWN" else "info",
                source=kit["callsign"],
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )
            return dict(kit)

    def mark_returned(self, kit_id: str) -> None:
        self._set_terminal(kit_id, "RETURNED")

    def mark_lost(self, kit_id: str) -> None:
        self._set_terminal(kit_id, "LOST")

    def _set_terminal(self, kit_id: str, status: str) -> None:
        with self._lock:
            kit = self.kits[kit_id]
            if kit["load_status"] in {"DELIVERED", "UNKNOWN", "LOST"}:
                return
            kit["load_status"] = status
            kit["final_status"] = status
            kit["updated_at"] = iso_now()
            self.db.upsert_kit(kit)
            self.bus.publish(
                "payload.ledger",
                {"kit_id": kit_id, "status": status, "callsign": kit["callsign"]},
                source="LEDGER",
                data_quality="SIMULATED" if self.bus.mission_mode == "SIMULATION" else "LIVE",
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            kits = [dict(k) for k in self.kits.values()]
        return {
            "kits": kits,
            "counts": self.counts(),
            "free": {c: self.free_count(c) for c in self.config.callsigns},
            "total": len(kits),
            "total_free": sum(1 for k in kits if k["load_status"] == "LOADED"),
            "policy": "A kit reaches DELIVERED only with CONFIRMED break-beam evidence; missing evidence is UNKNOWN and is never auto-retried.",
        }
