"""Role-specific preflight checks and the hard mission-readiness gate.

Automated checks are measured from telemetry, video, storage, plans and uploads.
Physical checks require an explicit operator sign-off and are never auto-passed.
Unknown is not pass. A failed or unknown blocking check keeps START locked.
"""

from __future__ import annotations

import threading
from typing import Any

from .config import Config
from .db import Database
from .events import EventBus
from .evidence import EvidenceStore
from .ledger import KitLedger
from .telemetry import TelemetryManager
from .util import iso_now

MANUAL_ITEMS = [
    {
        "check_id": "manual.airframe",
        "scope": "FLEET",
        "title": "Airframe, propeller and fastener inspection",
        "detail": "No cracks, loose fasteners, frame damage or cable strain. Physical check only.",
    },
    {
        "check_id": "manual.batteries",
        "scope": "FLEET",
        "title": "Battery secure and undamaged",
        "detail": "Batteries seated, connectors secure, no swelling or damage.",
    },
    {
        "check_id": "manual.payload_inventory",
        "scope": "FLEET",
        "title": "Ten kits loaded and physically counted",
        "detail": "Five kits per delivery aircraft. The break-beam alone does not prove inventory.",
    },
    {
        "check_id": "manual.release_test",
        "scope": "DELIVERY",
        "title": "Release gate and break-beam self-test",
        "detail": "Test the gate servo before kits are loaded; verify beam transitions on each DEL aircraft.",
    },
    {
        "check_id": "manual.rangefinder",
        "scope": "DELIVERY",
        "title": "TFmini-S ranging check",
        "detail": "Confirm a plausible height reading and correct orientation on each delivery aircraft.",
    },
    {
        "check_id": "manual.ins_camera",
        "scope": "INS-01",
        "title": "Pi Camera 3 Wide, gimbal and AI chain",
        "detail": "Nadir camera secure, gimbal free, Pi 5 / Hailo-8 / YOLOv8n process running.",
    },
    {
        "check_id": "manual.operator_video_identity",
        "scope": "FLEET",
        "title": "Operator video identity confirmed",
        "detail": "Each RunCam feed was visually confirmed against its aircraft during commissioning.",
    },
    {
        "check_id": "manual.rc_link",
        "scope": "FLEET",
        "title": "Independent RC safety link verified",
        "detail": "Safety pilot has positive control and failsafe behaviour is configured.",
    },
    {
        "check_id": "manual.pad_clear",
        "scope": "FLEET",
        "title": "Launch/landing pad clear",
        "detail": "Pad, approach and landing spots clear of people, vehicles and loose objects.",
    },
    {
        "check_id": "manual.emergency_controls",
        "scope": "FLEET",
        "title": "Emergency recall and abort rehearsal",
        "detail": "Operator and safety pilot have rehearsed RTL recall and LAND-in-place authority.",
    },
]


class PreflightEngine:
    def __init__(
        self,
        config: Config,
        db: Database,
        bus: EventBus,
        telemetry: TelemetryManager,
        ledger: KitLedger,
        evidence: EvidenceStore,
    ) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.telemetry = telemetry
        self.ledger = ledger
        self.evidence = evidence
        self._lock = threading.RLock()
        self.signoffs: dict[str, bool] = {}
        self.last_report: dict[str, Any] = {}
        self.video_snapshot: dict[str, Any] = {}
        self.devices: dict[str, Any] = {}

    def reset(self) -> None:
        with self._lock:
            self.signoffs.clear()
            self.last_report = {}

    def restore(self, mission_id: str) -> None:
        rows = self.db.get_preflight(mission_id)
        for row in rows:
            if row.get("evidence_source") == "MANUAL_SIGN_OFF" and row.get("result") == "PASS":
                self.signoffs[row["check_id"]] = True

    def manual_items(self) -> list[dict[str, Any]]:
        return [dict(item, signed=bool(self.signoffs.get(item["check_id"]))) for item in MANUAL_ITEMS]

    def sign(self, check_id: str, signed: bool, operator: str = "operator") -> dict[str, Any]:
        valid = {item["check_id"] for item in MANUAL_ITEMS}
        if check_id not in valid:
            raise ValueError(f"Unknown manual check: {check_id}")
        with self._lock:
            if signed:
                self.signoffs[check_id] = True
            else:
                self.signoffs.pop(check_id, None)
        self.bus.publish(
            "operator.action",
            {"action": "MANUAL_SIGN_OFF", "check_id": check_id, "signed": signed, "operator": operator},
            source="OPERATOR",
            data_quality="MANUAL",
        )
        mission_id = self.bus.mission_id
        if mission_id:
            self.db.record_operator_action(
                mission_id, "MANUAL_SIGN_OFF", operator, check_id, "SIGNED" if signed else "UNSIGNED"
            )
        return {"check_id": check_id, "signed": signed}

    def sign_all_simulation(self, operator: str = "operator") -> dict[str, Any]:
        if self.bus.mission_mode != "SIMULATION":
            raise PermissionError("Bulk simulation sign-off is only available in SIMULATION mode")
        for item in MANUAL_ITEMS:
            self.sign(item["check_id"], True, operator)
        return {"signed": len(MANUAL_ITEMS), "mode": "SIMULATION", "label": "SIMULATED CREW SIGN-OFF"}

    # ---------------------------------------------------------------- evaluate
    def evaluate(
        self,
        *,
        plan: dict[str, Any] | None,
        upload: dict[str, Any] | None,
        aoi: dict[str, Any] | None,
        phase: str,
        mode: str,
        video_snapshot: dict[str, Any] | None = None,
        devices: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if video_snapshot is not None:
            self.video_snapshot = video_snapshot
        if devices is not None:
            self.devices = devices
        checks: list[dict[str, Any]] = []
        quality = "SIMULATED" if mode == "SIMULATION" else "LIVE"

        def add(
            check_id: str,
            scope: str,
            title: str,
            result: str,
            *,
            required: bool = True,
            method: str = "AUTOMATED",
            measured: Any = "",
            threshold: Any = "",
            detail: str = "",
            evidence: str | None = None,
        ) -> None:
            checks.append({
                "check_id": check_id,
                "scope": scope,
                "title": title,
                "result": result,
                "required": required,
                "blocking": required and result in {"FAIL", "UNKNOWN", "MANUAL_PENDING"},
                "method": method,
                "measured": measured,
                "threshold": threshold,
                "detail": detail,
                "evidence_source": evidence or method,
                "data_quality": quality,
                "timestamp": iso_now(),
            })

        fleet = self.config.callsigns
        # ---------------------------------------------------------- fleet basic
        for profile in self.config.fleet:
            callsign = profile["callsign"]
            state = self.telemetry.vehicle(callsign)
            add(
                f"fleet.identity.{callsign}", callsign, "Vehicle identity and heartbeat",
                "PASS" if state["connected"] and not state["stale"] and state["identity_valid"] else "FAIL",
                measured=state.get("identity_note"), threshold="Valid heartbeat, correct SYSID/type",
                detail=f"SYSID {profile['sysid']} / {profile['role']}",
            )
            battery = state.get("battery_pct")
            launch_min = float(self.config.get("mission", "min_battery_to_launch_pct", default=55.0))
            add(
                f"fleet.battery.{callsign}", callsign, "Battery reserve",
                "PASS" if battery is not None and float(battery) >= launch_min else "FAIL",
                measured=f"{battery:.0f}%" if battery is not None else "unavailable",
                threshold=f"≥ {launch_min:.0f}% before launch",
            )
            add(
                f"fleet.gnss.{callsign}", callsign, "GNSS fix and satellite count",
                "PASS" if (state.get("gps_fix") or 0) >= 3 and (state.get("gps_sats") or 0) >= 6 else "FAIL",
                measured=f"fix {state.get('gps_fix')} / {state.get('gps_sats')} sats",
                threshold="3D fix and ≥ 6 satellites",
            )
            add(
                f"fleet.ekf.{callsign}", callsign, "EKF health",
                "PASS" if state.get("ekf_ok") is True else ("UNKNOWN" if state.get("ekf_ok") is None else "FAIL"),
                measured=state.get("ekf_ok"), threshold="EKF healthy",
            )
            add(
                f"fleet.telemetry.{callsign}", callsign, "Telemetry freshness",
                "PASS" if state["connected"] and not state["stale"] else "FAIL",
                measured=state.get("heartbeat_age_s"), threshold="≤ 2 s stale / 3 s link loss",
            )

        # --------------------------------------------------------------- radio
        radio_verified = bool(self.config.get("telemetry", "topology_verified", default=False))
        if mode == "SIMULATION":
            radio_verified = True
        add(
            "fleet.radio_topology", "FLEET", "RFD868x topology verified",
            "PASS" if radio_verified else "FAIL",
            measured="SIMULATED LINK" if mode == "SIMULATION" else self.config.get("telemetry", "topology"),
            threshold="Commissioned 3-aircraft test",
            detail="One shared multipoint link or three independent point-to-point links must be evidenced.",
        )

        # --------------------------------------------------------------- video
        video_required = bool(self.config.get("video", "required_for_readiness", default=True))
        feeds = (self.video_snapshot or {}).get("feeds", [])
        if mode == "SIMULATION":
            video_pass = len(feeds) == 3 and all(f.get("status") == "LIVE" for f in feeds)
            video_detail = "Three simulated RunCam feeds active"
        else:
            video_pass = len(feeds) == 3 and all(
                f.get("mapped") and f.get("status") in {"LIVE", "RECORDING"} for f in feeds
            )
            video_detail = "Each feed must be mapped and live"
        add(
            "fleet.video", "FLEET", "Three operator video feeds",
            "PASS" if video_pass or not video_required else "FAIL",
            required=video_required,
            measured=f"{sum(1 for f in feeds if f.get('status') in {'LIVE','RECORDING'})}/3 live",
            threshold="3/3 mapped and live",
            detail=video_detail,
        )
        add(
            "fleet.video_mapping", "FLEET", "Receiver-to-aircraft mapping",
            "PASS" if all(f.get("mapped") for f in feeds) or mode == "SIMULATION" else "UNKNOWN",
            measured="SIMULATED" if mode == "SIMULATION" else "commissioning required",
            threshold="Visual identity confirmed per feed",
            detail="Device enumeration order alone is not accepted as identity.",
        )

        # --------------------------------------------------------------- storage
        free_mb = self.evidence.disk_free_mb()
        add(
            "system.storage", "FLEET", "Recording storage",
            "PASS" if free_mb >= 2000 else "FAIL",
            measured=f"{free_mb:.0f} MB free", threshold="≥ 2000 MB",
        )

        # ----------------------------------------------------------------- plan
        if plan:
            add(
                "mission.plan", "FLEET", "Mission plan generated and valid",
                "PASS" if plan.get("validation", {}).get("ok") else "FAIL",
                measured=f"{plan.get('search', {}).get('lane_count')} lanes",
                threshold="Valid geometry and waypoints",
                detail="; ".join(plan.get("validation", {}).get("errors", [])) or "Plan validated",
            )
            estimate = plan.get("estimate", {})
            add(
                "mission.time", "FLEET", "Estimated mission inside time cap",
                "PASS" if estimate.get("within_cap") else "FAIL",
                measured=estimate.get("total_min_display"),
                threshold=f"≤ {estimate.get('time_cap_min', 30):.0f} min cap",
                evidence="CALCULATED",
            )
            add(
                "mission.pad", "FLEET", "Launch pad location",
                "PASS" if plan.get("pad") else "FAIL",
                measured=f"{plan.get('pad', {}).get('distance_to_boundary_m')} m from boundary",
                threshold="Configured pad, documented clearance",
                evidence="CONFIGURED",
            )
        else:
            add("mission.plan", "FLEET", "Mission plan generated", "FAIL",
                measured="no plan", threshold="Valid KML and plan", detail="Import a KML and generate a plan.")

        # --------------------------------------------------------------- upload
        receipts = (upload or {}).get("aircraft", {})
        for callsign in fleet:
            receipt = receipts.get(callsign, {})
            verified = receipt.get("status") == "VERIFIED"
            add(
                f"mission.upload.{callsign}", callsign, "Mission and fence upload verified",
                "PASS" if verified else "FAIL",
                measured=receipt.get("status", "NOT_UPLOADED"),
                threshold="Read-back matches uploaded plan",
                detail=receipt.get("detail", ""),
            )

        # -------------------------------------------------------- role specific
        ins = self.config.search_vehicle()
        ins_state = self.telemetry.vehicle(ins["callsign"])
        if mode == "SIMULATION":
            add("role.ins.ai", ins["callsign"], "Pi 5 / Hailo-8 / YOLOv8n chain", "PASS",
                measured="SIMULATED", threshold="Process alive and inference active",
                evidence="SIMULATED")
        else:
            add("role.ins.ai", ins["callsign"], "Pi 5 / Hailo-8 / YOLOv8n chain", "UNKNOWN",
                measured="no health channel configured", threshold="Process alive and inference active",
                detail="A Pi/Hailo health channel must be wired and verified before REAL launch.")
        for profile in self.config.delivery_vehicles():
            callsign = profile["callsign"]
            state = self.telemetry.vehicle(callsign)
            if mode == "SIMULATION":
                add(f"role.del.release.{callsign}", callsign, "Release mechanism and break-beam", "PASS",
                    measured="SIMULATED", threshold="Gate and beam self-test", evidence="SIMULATED")
            else:
                add(f"role.del.release.{callsign}", callsign, "Release mechanism and break-beam", "UNKNOWN",
                    measured="self-test not instrumented", threshold="Gate and beam self-test",
                    detail="Requires bench self-test and signed evidence.")

        # ------------------------------------------------------- payload ledger
        add(
            "payload.inventory", "FLEET", "Kit inventory available",
            "PASS" if self.ledger.total_free() == self.ledger.total() and self.ledger.total() == 10 else "FAIL",
            measured=f"{self.ledger.total_free()}/{self.ledger.total()} free",
            threshold="10 kits loaded, 0 reserved",
        )

        # ------------------------------------------------------------- manual
        for item in MANUAL_ITEMS:
            signed = bool(self.signoffs.get(item["check_id"]))
            add(
                item["check_id"], item["scope"], item["title"],
                "PASS" if signed else "MANUAL_PENDING",
                method="MANUAL",
                measured="signed" if signed else "not signed",
                threshold="Operator sign-off",
                detail=item["detail"],
                evidence="MANUAL_SIGN_OFF" if signed else "MANUAL_REQUIRED",
            )

        blocking = [check for check in checks if check["blocking"]]
        counts: dict[str, int] = {}
        for check in checks:
            counts[check["result"]] = counts.get(check["result"], 0) + 1
        ready = not blocking
        report = {
            "ready": ready,
            "checks": checks,
            "counts": counts,
            "blocking": blocking,
            "manual_pending": [check["check_id"] for check in checks if check["result"] == "MANUAL_PENDING"],
            "generated_at": iso_now(),
            "mode": mode,
            "phase": phase,
            "result_model": ["PASS", "FAIL", "WARNING", "MANUAL", "UNKNOWN"],
            "policy": "Unknown is not pass. Physical checks require sign-off. ArduPilot pre-arm checks remain authoritative.",
        }
        self.last_report = report
        mission_id = self.bus.mission_id
        if mission_id:
            self.db.save_preflight(mission_id, checks)
        return report
