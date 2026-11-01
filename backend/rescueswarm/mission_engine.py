"""Authoritative mission lifecycle and the hard readiness gate.

The mission engine owns the phase machine, the imported boundary, the plan, the
upload receipts, readiness and start authority. It is the only caller allowed to
start fleet execution, abort it or trigger an emergency recall. All of these
actions are logged with their inputs.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any

from .allocation import TaskAllocator
from .config import Config
from .db import Database
from .delivery import DeliveryEngine
from .events import EventBus
from .evidence import EvidenceStore
from .kml import KmlError, parse_kml
from .ledger import KitLedger, LedgerError
from .planner import PlanningError, generate_plan
from .preflight import PreflightEngine
from .safety import SafetyEngine
from .survivors import SurvivorRegistry
from .telemetry import (
    MAV_CMD_COMPONENT_ARM_DISARM,
    MAV_CMD_DO_SET_MODE,
    MAV_CMD_NAV_TAKEOFF,
    TelemetryManager,
)
from .upload import MissionUploader
from .util import iso_now, new_id
from .video import VideoManager

PHASES = [
    "SETUP", "PLANNING", "INSPECTION", "PREFLIGHT", "UPLOAD",
    "READY", "EXECUTING", "COMPLETE", "ABORTED",
]


class MissionError(RuntimeError):
    pass


class MissionEngine:
    def __init__(
        self,
        config: Config,
        db: Database,
        bus: EventBus,
        evidence: EvidenceStore,
        telemetry: TelemetryManager,
        video: VideoManager,
        survivors: SurvivorRegistry,
        ledger: KitLedger,
        allocator: TaskAllocator,
        safety: SafetyEngine,
        delivery: DeliveryEngine,
        preflight: PreflightEngine,
        uploader: MissionUploader,
    ) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.evidence = evidence
        self.telemetry = telemetry
        self.video = video
        self.survivors = survivors
        self.ledger = ledger
        self.allocator = allocator
        self.safety = safety
        self.delivery = delivery
        self.preflight = preflight
        self.uploader = uploader
        self.simulation: Any = None
        self.reporter: Any = None

        self._lock = threading.RLock()
        self.mode = "SIMULATION"
        self.phase = "SETUP"
        self.mission_id: str | None = None
        self.name: str | None = None
        self.aoi: dict[str, Any] | None = None
        self.plan: dict[str, Any] | None = None
        self.upload: dict[str, Any] = {}
        self.readiness: dict[str, Any] = {}
        self.started_at: str | None = None
        self.ended_at: str | None = None
        self.outcome: str | None = None
        self.recall = False
        self.last_survivor_report: dict[str, Any] | None = None
        self.completion_summary: dict[str, Any] | None = None

    # ------------------------------------------------------------------ setup
    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in {"SIMULATION", "REAL"}:
            raise ValueError("Mode must be SIMULATION or REAL")
        with self._lock:
            self.mode = mode
            self.bus.set_mission(self.mission_id, mode)
        self.bus.publish("system.mode", {"mode": mode}, source="GCS", data_quality=mode)

    def reset_session(self) -> None:
        with self._lock:
            self.phase = "SETUP"
            self.mission_id = None
            self.name = None
            self.aoi = None
            self.plan = None
            self.upload = {}
            self.readiness = {}
            self.started_at = None
            self.ended_at = None
            self.outcome = None
            self.recall = False
            self.last_survivor_report = None
            self.completion_summary = None
        self.survivors.reset()
        self.allocator.reset()
        self.ledger.reset()
        self.delivery.reset()
        self.preflight.reset()
        self.safety.set_recall(False)
        self.bus.set_mission(None, self.mode)

    # ------------------------------------------------------------------- KML
    def import_kml(self, content: bytes, filename: str, operator: str = "operator") -> dict[str, Any]:
        if self.phase == "EXECUTING":
            raise MissionError("Cannot import a new KML while a mission is executing")
        try:
            aoi = parse_kml(content, filename)
        except KmlError as exc:
            self.bus.publish("mission.import_failed", {"filename": filename, "reason": str(exc)},
                             severity="error", source="MISSION", data_quality="SYSTEM")
            raise MissionError(str(exc)) from exc
        self.reset_session()
        with self._lock:
            mission_id = f"MSN-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{new_id('X')[-4:]}"
            self.mission_id = mission_id
            self.name = aoi.get("name") or filename
            self.aoi = aoi
            self.phase = "PLANNING"
        self.bus.set_mission(mission_id, self.mode)
        self.db.create_mission({
            "mission_id": mission_id,
            "name": self.name,
            "mode": self.mode,
            "phase": "PLANNING",
            "state": "PREPARING",
            "created_at": iso_now(),
            "config_version": self.config.get("profile_version"),
            "notes": "KML imported; plan not yet generated",
        })
        self.ledger.initialise(mission_id)
        self.survivors.bind_aoi(aoi)
        self.delivery.bind_aoi(aoi)
        self.evidence.ensure_manifest({
            "mission_id": mission_id,
            "name": self.name,
            "mode": self.mode,
            "created_at": iso_now(),
            "config_version": self.config.get("profile_version"),
        })
        self.evidence.write_json(mission_id, "plan/aoi.json", aoi)
        self.db.record_operator_action(mission_id, "KML_IMPORT", operator, filename,
                                       f"{aoi.get('area_ha')} ha")
        self.bus.publish(
            "mission.imported",
            {"mission_id": mission_id, "filename": filename, "area_ha": aoi.get("area_ha"),
             "valid": aoi.get("valid"), "warnings": aoi.get("warnings", [])},
            source="MISSION", data_quality="SYSTEM",
        )
        return self.status()

    # ------------------------------------------------------------------ plan
    def create_plan(self, operator: str = "operator") -> dict[str, Any]:
        if not self.aoi:
            raise MissionError("Import a KML boundary before generating a plan")
        if self.phase == "EXECUTING":
            raise MissionError("Plan cannot change during execution")
        try:
            plan = generate_plan(self.aoi, self.config, self.mode)
        except PlanningError as exc:
            self.bus.publish("mission.plan_failed", {"reason": str(exc)},
                             severity="error", source="MISSION", data_quality="SYSTEM")
            raise MissionError(str(exc)) from exc
        with self._lock:
            self.plan = plan
            self.upload = {}
            self.readiness = {}
            self.phase = "PLANNING"
        self.db.save_plan(self.mission_id, plan)
        self.db.update_mission(self.mission_id, phase="PLANNING", plan_hash=plan["plan_hash"])
        self.evidence.write_json(self.mission_id, "plan/plan.json", plan)
        self.survivors.bind_aoi(self.aoi)
        self.delivery.bind_aoi(self.aoi)
        self.db.record_operator_action(self.mission_id, "PLAN_GENERATE", operator, plan["plan_id"],
                                       plan["plan_hash"])
        self.bus.publish(
            "mission.planned",
            {"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"],
             "lanes": plan["search"]["lane_count"], "estimate": plan["estimate"],
             "validation": plan["validation"]},
            source="PLANNER", data_quality="CALCULATED",
        )
        return self.status()

    # -------------------------------------------------------------- inspection
    def sign_manual(self, check_id: str, signed: bool, operator: str = "operator") -> dict[str, Any]:
        result = self.preflight.sign(check_id, signed, operator)
        if self.phase in {"SETUP", "PLANNING"}:
            self._set_phase("INSPECTION")
        return result

    def sign_all_simulation(self, operator: str = "operator") -> dict[str, Any]:
        result = self.preflight.sign_all_simulation(operator)
        self._set_phase("INSPECTION")
        return result

    # --------------------------------------------------------------- preflight
    def run_preflight(self, operator: str = "operator") -> dict[str, Any]:
        if not self.mission_id:
            raise MissionError("No mission session")
        if self.phase in {"COMPLETE", "ABORTED"}:
            raise MissionError("Start a new mission before running preflight for another sortie")
        report = self._evaluate_preflight(phase="PREFLIGHT")
        if self.phase in {"SETUP", "PLANNING", "INSPECTION"}:
            self._set_phase("PREFLIGHT")
        self.db.record_operator_action(self.mission_id, "PREFLIGHT_RUN", operator, None,
                                       "READY" if report["ready"] else "BLOCKED")
        self.bus.publish(
            "preflight.completed",
            {"ready": report["ready"], "counts": report["counts"],
             "blocking": [c["check_id"] for c in report["blocking"]]},
            severity="info" if report["ready"] else "warning",
            source="PREFLIGHT", data_quality="SYSTEM",
        )
        return report

    def _evaluate_preflight(self, phase: str) -> dict[str, Any]:
        report = self.preflight.evaluate(
            plan=self.plan,
            upload=self.upload,
            aoi=self.aoi,
            phase=phase,
            mode=self.mode,
            video_snapshot=self.video.health(),
            devices=self.video.device_snapshot(),
        )
        self.readiness = report
        return report

    # ----------------------------------------------------------------- upload
    async def upload_mission(self, operator: str = "operator") -> dict[str, Any]:
        if not self.plan:
            raise MissionError("Generate a mission plan before upload")
        if self.phase in {"COMPLETE", "ABORTED"}:
            raise MissionError("Start a new mission before uploading another plan")
        if self.mode == "REAL" and not self.telemetry.connected:
            raise MissionError("Connect the telemetry fleet before uploading")
        self._set_phase("UPLOAD")
        self.bus.publish("mission.upload_started", {"plan_id": self.plan["plan_id"]},
                         source="MISSION", data_quality="LIVE" if self.mode == "REAL" else "SIMULATED")
        try:
            receipts = await asyncio.to_thread(self.uploader.upload_plan, self.plan, self.mode)
        except Exception as exc:
            receipts = {"aircraft": {}, "error": str(exc), "mode": self.mode}
        with self._lock:
            self.upload = receipts
        self.db.save_plan(self.mission_id, self.plan)
        self.db.set_plan_upload_state(self.plan["plan_id"], receipts)
        self.evidence.write_json(self.mission_id, "plan/upload_receipts.json", receipts)
        self.db.record_operator_action(self.mission_id, "MISSION_UPLOAD", operator,
                                       self.plan["plan_id"],
                                       "VERIFIED" if self._upload_verified() else "UNVERIFIED")
        verified = self._upload_verified()
        self.bus.publish(
            "mission.uploaded",
            {"verified": verified, "receipts": receipts},
            severity="info" if verified else "warning",
            source="MISSION", data_quality="SIMULATED" if self.mode == "SIMULATION" else "LIVE",
        )
        return {"verified": verified, "receipts": receipts, "status": self.status()}

    def _upload_verified(self) -> bool:
        receipts = (self.upload or {}).get("aircraft", {})
        if not receipts:
            return False
        if self.mode == "REAL" and any(r.get("simulated") for r in receipts.values()):
            return False
        for callsign in self.config.callsigns:
            receipt = receipts.get(callsign)
            if not receipt or receipt.get("status") != "VERIFIED":
                return False
        return True

    # ------------------------------------------------------------------- gate
    def gate(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add(check_id: str, title: str, ok: bool, detail: str, blocking: bool = True) -> None:
            checks.append({"check_id": check_id, "title": title, "ok": bool(ok),
                           "detail": detail, "blocking": blocking})

        add("session", "Mission session exists", self.mission_id is not None,
            self.mission_id or "No imported boundary")
        add("plan", "Valid mission plan", bool(self.plan and self.plan.get("validation", {}).get("ok")),
            self.plan["plan_id"] if self.plan else "No plan")
        add("upload", "Upload and read-back verified", self._upload_verified(),
            "All aircraft receipts VERIFIED" if self._upload_verified() else "Upload not verified")
        fresh = self.telemetry.freshness_report()
        total_aircraft = len(self.config.callsigns)
        add("telemetry", "Fleet telemetry fresh and identified", self.telemetry.all_fresh(),
            "All aircraft links live and identified" if not fresh else
            "; ".join(f"{callsign}: {why}" for callsign, why in fresh.items())
            if len(fresh) <= total_aircraft else f"{len(fresh)} links not fresh",
        )
        add("mode", "Telemetry mode matches mission mode",
            self.telemetry.mode == self.mode and self.telemetry.connected,
            f"telemetry={self.telemetry.mode}, mission={self.mode}")
        add("recall", "Emergency recall not active", not self.safety.recall_active,
            "Recall active" if self.safety.recall_active else "Clear")
        pad = self.safety.pad_status(self.telemetry.vehicles)
        add("pad", "Launch pad free for first launch", not pad["violation"],
            ", ".join(o["callsign"] for o in pad["occupants"]) or "No armed aircraft on pad")
        add("kits", "Ten kits loaded", self.ledger.total() == 10 and self.ledger.total_free() == 10,
            f"{self.ledger.total_free()}/{self.ledger.total()} free")
        add("phase", "Mission not already executing", self.phase not in {"EXECUTING", "COMPLETE"},
            f"current phase {self.phase}")

        preflight = self._evaluate_preflight("READINESS")
        add("preflight", "Preflight and manual sign-offs complete", preflight.get("ready", False),
            f"{len(preflight.get('blocking', []))} blocking checks")
        blockers = [check for check in checks if check["blocking"] and not check["ok"]]
        if preflight.get("blocking"):
            blockers.extend(preflight["blocking"])
        ready = not blockers
        if ready:
            self._set_phase("READY")
        result = {
            "ready": ready,
            "checks": checks,
            "blockers": blockers,
            "preflight": preflight,
            "generated_at": iso_now(),
            "policy": "START is unlocked only by this gate. Unknown is not pass.",
        }
        return result

    # ------------------------------------------------------------------ start
    def start_mission(self, operator: str = "operator", confirmation: str = "") -> dict[str, Any]:
        gate = self.gate()
        if not gate["ready"]:
            self.bus.publish(
                "mission.start_denied",
                {"blockers": [b.get("check_id") for b in gate["blockers"]], "operator": operator},
                severity="warning", source="MISSION", data_quality="SYSTEM",
            )
            self.db.record_operator_action(self.mission_id, "MISSION_START", operator, None, "DENIED")
            raise MissionError("Readiness gate is not satisfied: " + ", ".join(
                str(b.get("check_id")) for b in gate["blockers"]
            ))
        if confirmation != "START":
            raise MissionError("Start requires the explicit confirmation token START")
        with self._lock:
            self.phase = "EXECUTING"
            self.started_at = iso_now()
            self.ended_at = None
            self.outcome = None
            self.recall = False
        self.db.update_mission(self.mission_id, phase="EXECUTING", state="ACTIVE", started_at=self.started_at)
        self.db.record_operator_action(self.mission_id, "MISSION_START", operator, None, "APPROVED")
        self.video.start_recording(self.mission_id)
        if self.mode == "SIMULATION":
            if self.simulation:
                self.simulation.start(self.plan, self.aoi)
            self.bus.publish(
                "mission.started",
                {"mission_id": self.mission_id, "mode": "SIMULATION",
                 "label": "SIMULATED AUTONOMOUS MISSION — NOT FLIGHT DATA"},
                severity="critical", source="MISSION", data_quality="SIMULATED",
            )
        else:
            self._send_real_launch()
            self.bus.publish(
                "mission.started",
                {"mission_id": self.mission_id, "mode": "REAL"},
                severity="critical", source="MISSION", data_quality="LIVE",
            )
        return self.status()

    def _send_real_launch(self) -> None:
        ins = self.config.search_vehicle()["callsign"]
        alt = float(self.config.search_vehicle()["search_alt_m"])
        pad = self.plan["pad"]
        self.telemetry.send_command(ins, MAV_CMD_COMPONENT_ARM_DISARM, [1, 0, 0, 0, 0, 0, 0],
                                    "Arm INS-01 for search")
        self.telemetry.send_command(ins, MAV_CMD_NAV_TAKEOFF,
                                    [0, 0, 0, 0, pad["lat"], pad["lon"], alt],
                                    "Take off INS-01")

    # -------------------------------------------------------------- execution
    def on_survivor_report(self, aircraft: str, envelope: dict[str, Any],
                           data_quality: str = "SIMULATED") -> dict[str, Any] | None:
        if self.phase != "EXECUTING" or not self.mission_id:
            return None
        survivor = self.survivors.ingest_report(aircraft, envelope, data_quality=data_quality)
        if not survivor:
            return None
        self.last_survivor_report = survivor
        if survivor.get("status") != "CONFIRMED":
            return {"survivor": survivor, "allocated": False, "reason": "Already handled"}
        decision = self.allocator.allocate(survivor["survivor_id"], self.telemetry.vehicles)
        if decision.get("allocated"):
            self._activate_assignment(decision["assignment"])
        return decision

    def _activate_assignment(self, assignment: dict[str, Any]) -> None:
        """Create the delivery task, dispatch it, and persist the full decision."""
        self.delivery.create(assignment)
        if self.simulation and self.mode == "SIMULATION":
            self.simulation.register_assignment(assignment)
        elif self.mode == "REAL":
            self._dispatch_real_delivery(assignment)
        self.evidence.append_jsonl(self.mission_id, "survivors/allocations.jsonl",
                                   {"at": iso_now(), "decision": assignment.get("decision", {})})

    def retry_unserved(self) -> list[dict[str, Any]]:
        """Retry allocation for survivors that no aircraft could serve earlier.

        Only UNSERVED survivors are considered. They hold no kit reservation, so
        retrying cannot double-book; the ledger reserve remains the atomic gate.
        The caller decides when to retry (simulation: when a delivery aircraft is
        parked; REAL mode: an explicit operator request).
        """
        if self.phase != "EXECUTING" or not self.mission_id:
            return []
        attempts: list[dict[str, Any]] = []
        for survivor in self.survivors.list():
            if survivor["status"] != "UNSERVED":
                continue
            if self.delivery.for_survivor(survivor["survivor_id"]):
                continue
            decision = self.allocator.allocate(survivor["survivor_id"], self.telemetry.vehicles)
            attempts.append(decision)
            if decision.get("allocated"):
                self._activate_assignment(decision["assignment"])
                self.bus.publish(
                    "allocation.retried",
                    {"survivor_id": survivor["survivor_id"],
                     "callsign": decision["assignment"]["callsign"],
                     "assignment_id": decision["assignment"]["assignment_id"]},
                    source="ALLOCATOR",
                    data_quality="SIMULATED" if self.mode == "SIMULATION" else "LIVE",
                )
        return attempts

    def _dispatch_real_delivery(self, assignment: dict[str, Any]) -> None:
        """Arm and launch a delivery aircraft in GUIDED mode.

        The operator/safety pilot retains authority; this command sequence is a
        PENDING TEST implementation and is never asserted as verified flight.
        """
        callsign = assignment["callsign"]
        alt = float(self.config.vehicle(callsign)["transit_alt_m"])
        pad = self.plan["pad"]
        self.telemetry.send_command(callsign, MAV_CMD_COMPONENT_ARM_DISARM, [1, 0, 0, 0, 0, 0, 0],
                                    f"Arm {callsign} for delivery")
        self.telemetry.send_command(callsign, MAV_CMD_NAV_TAKEOFF,
                                    [0, 0, 0, 0, pad["lat"], pad["lon"], alt],
                                    f"Take off {callsign} to transit altitude")

    def on_project_event(self, aircraft: str, envelope: dict[str, Any]) -> None:
        event_type = str(envelope.get("type") or envelope.get("event_type") or "").lower()
        if event_type in {"survivor", "survivor_report", "detection"}:
            if self.phase == "EXECUTING":
                self.on_survivor_report(aircraft, envelope, data_quality="LIVE")
        elif event_type in {"payload_release", "release", "break_beam"}:
            self.handle_beam_event(envelope, data_quality="LIVE")
        elif event_type in {"ai_health", "health"}:
            self.bus.publish("ai.health", {"aircraft": aircraft, **envelope},
                             source=aircraft, data_quality="LIVE")

    def handle_beam_event(self, envelope: dict[str, Any], data_quality: str = "SIMULATED") -> dict[str, Any]:
        """Turn a break-beam report into delivery evidence.

        An event with no id cannot be attributed to anything and is refused: silent
        evidence is how a kit gets marked delivered that never left the aircraft.
        """
        assignment_id = envelope.get("assignment_id")
        kit_id = envelope.get("kit_id")
        if not assignment_id and kit_id:
            kit = self.ledger.kits.get(kit_id)
            if kit and kit.get("reserved_for"):
                assignment = self.allocator.assignment_for(kit["reserved_for"])
                assignment_id = assignment["assignment_id"] if assignment else None
        if not assignment_id:
            return {"accepted": False, "reason": "No matching assignment"}
        event_id = envelope.get("event_id") or envelope.get("id")
        if not event_id:
            self.bus.publish(
                "payload.denied",
                {"assignment_id": assignment_id, "kit_id": kit_id,
                 "reasons": ["Break-beam event carries no event id; unattributable evidence is refused"],
                 "raw": {k: v for k, v in envelope.items() if k in {"beam_result", "result", "seq"}}},
                severity="critical", source="SAFETY", data_quality=data_quality,
            )
            return {"accepted": False, "reason": "Break-beam event has no event id"}
        beam = str(envelope.get("beam_result") or envelope.get("result") or "UNKNOWN").upper()
        if beam not in {"CONFIRMED", "NO_BEAM", "SENSOR_FAULT", "UNKNOWN"}:
            beam = "UNKNOWN"
        try:
            result = self.delivery.apply_beam_result(assignment_id, beam,
                                                     event_id=str(event_id))
        except LedgerError as exc:
            self.bus.publish(
                "payload.denied",
                {"assignment_id": assignment_id, "kit_id": kit_id, "reasons": [str(exc)]},
                severity="critical", source="LEDGER", data_quality=data_quality,
            )
            return {"accepted": False, "reason": str(exc)}
        self.bus.publish(
            "payload.verified",
            {"assignment_id": assignment_id, "beam_result": beam, "result": result},
            severity="warning" if not result.get("confirmed") else "info",
            source="DELIVERY", data_quality=data_quality,
        )
        return result

    def on_simulation_complete(self, metrics: dict[str, Any]) -> None:
        if self.phase != "EXECUTING":
            return
        outcome = str(metrics.get("outcome") or "SIMULATION_COMPLETE")
        self.finalize(outcome, metrics=metrics)

    # ------------------------------------------------------------- emergency
    def emergency_recall(self, operator: str = "operator", reason: str = "Operator recall") -> dict[str, Any]:
        if self.phase != "EXECUTING":
            raise MissionError("Emergency recall requires an executing mission")
        self.recall = True
        self.safety.set_recall(True, reason)
        self.db.record_operator_action(self.mission_id, "EMERGENCY_RECALL", operator, None, reason)
        if self.simulation and self.mode == "SIMULATION":
            self.simulation.recall(reason)
        else:
            for callsign in self.config.callsigns:
                self.telemetry.send_command(callsign, 20, [0, 0, 0, 0, 0, 0, 0],
                                            f"Emergency RTL — {reason}")
        self.bus.publish("mission.recall", {"operator": operator, "reason": reason},
                         severity="critical", source="MISSION", data_quality="SIMULATED"
                         if self.mode == "SIMULATION" else "LIVE")
        return {"recall": True, "reason": reason, "status": self.status()}

    def abort(self, operator: str = "operator", reason: str = "Operator abort") -> dict[str, Any]:
        if self.phase != "EXECUTING":
            raise MissionError("Abort requires an executing mission")
        if self.simulation and self.mode == "SIMULATION":
            self.simulation.abort(reason)
        else:
            for callsign in self.config.callsigns:
                self.telemetry.send_command(callsign, MAV_CMD_DO_SET_MODE, [1, 5, 0, 0, 0, 0, 0],
                                            f"Abort — hold LOITER on {callsign}")
        returned, unknown = self._reconcile_kits_on_abort(reason)
        self.db.record_operator_action(self.mission_id, "MISSION_ABORT", operator, None, reason)
        status = self.finalize("ABORTED", reason=reason)
        status = dict(status)
        status.update({
            "kits_returned_to_magazine": returned,
            "kits_unverified": unknown,
            "note": "Abort holds the fleet in LOITER. Kits without beam evidence are UNKNOWN "
                    "and are never re-released automatically.",
        })
        return status

    def _reconcile_kits_on_abort(self, reason: str) -> tuple[list[str], list[str]]:
        """Leave no kit reserved or half-released when a sortie is aborted.

        A kit whose release was never commanded is still physically aboard, so it goes
        back to the pool and its survivor becomes unserved again. A kit that was commanded
        away without beam evidence is UNKNOWN for good: aborting creates no evidence and
        must not re-fire the latch.
        """
        returned: list[str] = []
        unknown: list[str] = []
        for assignment in list(self.allocator.assignments.values()):
            task = self.delivery.get(assignment["assignment_id"])
            if not task:
                continue
            state = task["state"]
            if state in {"QUEUED", "LAUNCHING", "HOLD_CLEARANCE", "TRANSIT", "DESCENT", "HOVER_VERIFY"}:
                try:
                    self.delivery.transition(assignment["assignment_id"], "ABORTED", detail=reason)
                    self.delivery.transition(assignment["assignment_id"], "RETURNING",
                                             detail="Abort recovery")
                except ValueError:
                    pass
                continue
            if state not in {"ACK_PENDING", "RELEASE_SENT"}:
                continue
            kit = self.ledger.kits.get(task["kit_id"])
            if not kit:
                continue
            tx = self.telemetry.transaction(task["pending_tx_id"]) if task.get("pending_tx_id") else None
            if kit["load_status"] == "RESERVED" and tx and tx.get("transmitted"):
                # The servo frame had already left the station when the abort landed. Whether
                # the latch fired is unknown, and "unknown" must never be recycled into
                # "still aboard" — that is how one physical kit gets promised to two people.
                self.ledger.note_command_abandoned(
                    task["kit_id"], reason=f"Abort during {state}: release frame already transmitted")
                unknown.append(task["kit_id"])
                try:
                    self.delivery.transition(assignment["assignment_id"], "UNKNOWN",
                                             detail=f"Abort during {state}: release frame transmitted, "
                                                    "no beam evidence",
                                             beam_result="NO_EVIDENCE")
                except ValueError:
                    pass
                continue
            if kit["load_status"] == "RESERVED":
                self.ledger.cancel_reservation(task["kit_id"], reason=f"Abort during {state}")
                returned.append(task["kit_id"])
                try:
                    self.delivery.transition(assignment["assignment_id"], "ABORTED",
                                             detail=f"Abort during {state}")
                except ValueError:
                    pass
            elif kit["load_status"] == "RELEASE_SENT":
                self.ledger.note_command_abandoned(
                    task["kit_id"], reason=f"Abort during {state} with no beam evidence")
                unknown.append(task["kit_id"])
                try:
                    self.delivery.transition(assignment["assignment_id"], "UNKNOWN",
                                             detail=f"Abort during {state}: no beam evidence",
                                             beam_result="NO_EVIDENCE")
                except ValueError:
                    pass
        for kit in list(self.ledger.kits.values()):
            if kit["load_status"] == "RESERVED":
                self.ledger.cancel_reservation(kit["kit_id"],
                                               reason="Abort with no active delivery task")
                returned.append(kit["kit_id"])
        return returned, unknown

    def clear_recall(self, operator: str = "operator") -> dict[str, Any]:
        """Clear the GCS recall flag.

        In SIMULATION the simulator resumes its own state machine. In REAL mode this
        only clears the ground-side latch: aircraft already in RTL stay in RTL, and the
        GCS has no authority over onboard failsafes by design.
        """
        self.recall = False
        self.safety.set_recall(False)
        simulator_resumed = False
        if self.simulation and self.mode == "SIMULATION" and self.simulation.running:
            self.simulation.clear_recall()
            simulator_resumed = True
        else:
            self.bus.publish(
                "safety.interlock",
                {"rule_id": "RECALL_CLEAR_REAL",
                 "detail": "Ground recall flag cleared. Aircraft already in RTL remain in RTL; "
                           "onboard failsafes are not under GCS control."},
                severity="warning", source="SAFETY", data_quality="LIVE",
            )
        self.db.record_operator_action(self.mission_id, "RECALL_CLEAR", operator, None, "")
        return {"recall": False, "simulator_resumed": simulator_resumed,
                "note": "Aircraft autonomy is never overridden from the ground."}

    # -------------------------------------------------------------- lifecycle
    def finalize(self, outcome: str, **detail: Any) -> dict[str, Any]:
        with self._lock:
            if self.phase not in {"EXECUTING", "ABORTED"}:
                return self.status()
            self.phase = "COMPLETE" if outcome != "ABORTED" else "ABORTED"
            self.outcome = outcome
            self.ended_at = iso_now()
        self.video.stop_recording()
        self.db.update_mission(
            self.mission_id, phase=self.phase, state="COMPLETED" if outcome != "ABORTED" else "ABORTED",
            ended_at=self.ended_at, outcome=outcome,
        )
        self.survivors_snapshot = self.survivors.snapshot()
        summary = {
            "mission_id": self.mission_id,
            "name": self.name,
            "mode": self.mode,
            "outcome": outcome,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "survivors": self.survivors.snapshot(),
            "kits": self.ledger.snapshot(),
            "delivery": self.delivery.snapshot(),
            "allocations": self.allocator.snapshot(),
            "recall": self.recall,
            "detail": detail,
            "evidence": "SIMULATED" if self.mode == "SIMULATION" else "LIVE",
        }
        self.completion_summary = summary
        self.evidence.write_json(self.mission_id, "reports/completion_summary.json", summary)
        if self.reporter:
            try:
                self.reporter.generate(self.mission_id, summary, self.plan, self.aoi)
            except Exception as exc:
                self.bus.publish("report.failed", {"reason": str(exc)},
                                 severity="warning", source="REPORT", data_quality="SYSTEM")
        self.bus.publish("mission.completed", summary,
                         severity="critical" if outcome != "ABORTED" else "warning",
                         source="MISSION",
                         data_quality="SIMULATED" if self.mode == "SIMULATION" else "LIVE")
        return self.status()

    # ------------------------------------------------------------------ status
    def _set_phase(self, phase: str) -> None:
        with self._lock:
            if self.phase == phase:
                return
            previous = self.phase
            self.phase = phase
        if self.mission_id:
            self.db.update_mission(self.mission_id, phase=phase)
        self.bus.publish("mission.phase", {"phase": phase, "previous": previous},
                         source="MISSION", data_quality="SYSTEM")

    def elapsed_s(self) -> float:
        if not self.started_at:
            return 0.0
        try:
            started = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.ended_at) if self.ended_at else datetime.now(timezone.utc)
            return max(0.0, (end - started).total_seconds())
        except ValueError:
            return 0.0

    def status(self, include_plan: bool = True) -> dict[str, Any]:
        plan_summary = None
        if self.plan:
            plan_summary = {
                "plan_id": self.plan["plan_id"],
                "plan_hash": self.plan["plan_hash"],
                "created_at": self.plan["created_at"],
                "search": self.plan["search"],
                "estimate": self.plan["estimate"],
                "energy": self.plan["energy"],
                "geofences": self.plan["geofences"],
                "pad": self.plan["pad"],
                "validation": self.plan["validation"],
                "mission_items": self.plan["mission_items"],
                "delivery": self.plan["delivery"],
            }
        simulation = self.simulation.snapshot() if self.simulation else None
        return {
            "mission_id": self.mission_id,
            "name": self.name,
            "mode": self.mode,
            "phase": self.phase,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed_s": round(self.elapsed_s(), 1),
            # The cap the engine actually enforces. The console used to print a hardcoded
            # "cap 30:00", which would have been a lie the moment the profile changed.
            "time_cap_min": float(self.config.get("mission", "time_cap_min", default=30.0)),
            # The same numbers the safety and survivor rules actually read, published so the
            # console can stop hard-coding them. A screen that prints "20 m" while the profile
            # says otherwise is a briefing lie about a safety limit.
            "limits": {
                "descent_clearance_m": float(self.config.get("mission", "descent_clearance_m", default=20.0)),
                "merge_distance_m": float(self.config.get("mission", "merge_distance_m", default=5.0)),
                "proximity_warn_m": float(self.config.get("mission", "proximity_warn_m", default=8.0)),
                "max_survivors": int(self.config.get("mission", "max_survivors", default=10)),
            },
            "recall": self.recall,
            "aoi": self.aoi,
            "plan": plan_summary if include_plan else None,
            "upload": self.upload,
            "upload_verified": self._upload_verified(),
            "readiness": {
                "ready": self.readiness.get("ready"),
                "counts": self.readiness.get("counts", {}),
                "blocking": [c["check_id"] for c in self.readiness.get("blocking", [])],
            },
            "completion_summary": self.completion_summary,
            "simulation": simulation,
            "evidence": "SIMULATED" if self.mode == "SIMULATION" else "LIVE",
        }
