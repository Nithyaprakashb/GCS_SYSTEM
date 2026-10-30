"""Deterministic three-aircraft simulation for the offline RescueSwarm GCS.

This is an engineering simulator, not a flight model. It exercises the mission
state machine, ETA allocation, delivery queue, pad interlock, clearance rules,
break-beam handling and mission completion with the same deterministic engines
used in REAL mode. Every value it produces is labelled SIMULATED and is never
presented as measured flight performance.
"""

from __future__ import annotations

import asyncio
import math
import random
import threading
import time
from typing import Any

from shapely.geometry import Point, Polygon

from .config import Config
from .db import Database
from .delivery import DeliveryEngine
from .events import EventBus
from .geo import bearing_deg, destination_m, haversine_m, LocalFrame
from .ledger import KitLedger
from .safety import SafetyEngine
from .survivors import SurvivorRegistry
from .telemetry import TelemetryManager
from .util import iso_now, safe_round

CLIMB_RATE = 2.5
DESCEND_RATE = 1.5
LAND_RATE = 1.2
HOVER_VERIFY_S = 4.0
BEAM_DELAY_S = 2.5
DETECTION_RADIUS_M = 32.0
DETECTION_CONFIRM_S = 2.0


class SimulationEngine:
    def __init__(
        self,
        config: Config,
        db: Database,
        bus: EventBus,
        telemetry: TelemetryManager,
        survivors: SurvivorRegistry,
        ledger: KitLedger,
        delivery: DeliveryEngine,
        safety: SafetyEngine,
    ) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.telemetry = telemetry
        self.survivors = survivors
        self.ledger = ledger
        self.delivery = delivery
        self.safety = safety
        self.mission: Any = None
        self._lock = threading.RLock()
        self._task: asyncio.Task | None = None
        self.running = False
        self.aborted = False
        self.recall_active = False
        self.recall_reason = ""
        self.sim_time_s = 0.0
        self.speed = float(config.get("simulation", "speed", default=8.0))
        self._tick_s = 0.1
        self._last_publish = 0.0
        self._rng = random.Random(int(config.get("simulation", "seed", default=71)))
        self.aircraft: dict[str, dict[str, Any]] = {}
        self.targets: list[dict[str, Any]] = []
        self._beam_queue: list[tuple[float, str]] = []
        self._duplicate_due: float | None = None
        self._completion_sent = False
        self._recall_issued = False
        self._last_retry = 0.0
        self.link_lost: set[str] = set()
        self.forced_beam: dict[str, str] = {}
        self.bench = False

    # ------------------------------------------------------------------ setup
    def _build_aircraft(self, pad: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        aircraft: dict[str, dict[str, Any]] = {}
        for profile in self.config.fleet:
            callsign = profile["callsign"]
            is_search = profile["role"] == "SEARCH"
            aircraft[callsign] = {
                "callsign": callsign,
                "role": profile["role"],
                "lat": pad["lat"],
                "lon": pad["lon"],
                "alt": 0.0,
                "armed": False,
                "mode": "STANDBY",
                "phase": "PARKED",
                "speed": 0.0,
                "heading": 0.0,
                "battery": 100.0,
                "task": "STANDBY",
                "queue": [],
                "current": None,
                "phase_since": 0.0,
                "hover_start": 0.0,
                "beam_due": None,
                "search_alt": float(profile.get("search_alt_m") or 12.0),
                "transit_alt": float(profile.get("transit_alt_m") or profile.get("search_alt_m") or 18.0),
                "drop_alt": float(profile.get("drop_height_m") or 3.0),
                "speed_cmd": float(profile.get("search_speed_ms") or profile.get("delivery_speed_ms") or 8.0),
                "waypoints": self._waypoints(plan) if (plan and is_search) else [],
                "wp_index": 0,
                "recall": False,
                "landed": not is_search,
            }
        return aircraft

    def start_bench(self) -> dict[str, Any]:
        """Run the simulated fleet at the pad before a mission is started.

        This emits clearly labelled SIMULATED heartbeats so the operator can
        inspect links, identity, video and preflight without waiting for launch.
        It deliberately plants no survivors and commands no movement.
        """
        self.stop()
        pad = {
            "lat": float(self.config.get("mission", "pad_lat")),
            "lon": float(self.config.get("mission", "pad_lon")),
        }
        self.sim_time_s = 0.0
        self.aborted = False
        self.recall_active = False
        self.recall_reason = ""
        self._completion_sent = True
        self._last_publish = 0.0
        self.targets = []
        self.aircraft = self._build_aircraft(pad)
        self.bench = True
        self.running = True
        self._apply_telemetry()
        self._task = asyncio.create_task(self._run())
        self.bus.publish(
            "simulation.bench",
            {"state": "AT_PAD", "aircraft": len(self.aircraft),
             "label": "SIMULATED telemetry — no mission executing"},
            source="SIMULATION", data_quality="SIMULATED",
        )
        return self.snapshot()

    def start(self, plan: dict[str, Any], aoi: dict[str, Any] | None) -> dict[str, Any]:
        self.stop()
        self.bench = False
        self.speed = float(self.config.get("simulation", "speed", default=8.0))
        self._rng = random.Random(int(self.config.get("simulation", "seed", default=71)))
        self.sim_time_s = 0.0
        self.aborted = False
        self.recall_active = False
        self.recall_reason = ""
        self._completion_sent = False
        self._recall_issued = False
        self._last_retry = 0.0
        self.link_lost.clear()
        self.forced_beam.clear()
        self._beam_queue.clear()
        self._duplicate_due = None
        self._last_publish = 0.0
        pad = plan["pad"]
        self.aircraft = self._build_aircraft(pad, plan)
        # The search aircraft launches first; delivery aircraft remain staged.
        ins = self.aircraft[self.config.search_vehicle()["callsign"]]
        self._set_phase(ins, "TAKEOFF", "Simulated autonomous takeoff")
        ins["armed"] = True
        ins["mode"] = "AUTO"
        ins["task"] = "SEARCH"
        self._targets_from_aoi(aoi)
        self.running = True
        self._apply_telemetry()
        self._task = asyncio.create_task(self._run())
        self.bus.publish(
            "simulation.started",
            {"speed": self.speed, "survivors_planted": len(self.targets),
             "label": "SIMULATED — deterministic engineering simulation"},
            severity="warning", source="SIMULATION", data_quality="SIMULATED",
        )
        return self.snapshot()

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.running = False
        self.bench = False

    def _waypoints(self, plan: dict[str, Any]) -> list[dict[str, float]]:
        points: list[dict[str, float]] = []
        for lane in plan.get("search", {}).get("lanes", []):
            points.extend(lane)
        return points

    def _targets_from_aoi(self, aoi: dict[str, Any] | None) -> None:
        count = int(self.config.get("simulation", "survivors", default=10))
        self.targets = []
        if not aoi or len(aoi.get("ring", [])) < 4:
            return
        ring = aoi["ring"]
        frame = LocalFrame.from_points(ring)
        polygon = Polygon([frame.to_local(lat, lon) for lon, lat, *_ in ring])
        inner = polygon.buffer(-8.0)
        if inner.is_empty:
            inner = polygon
        minx, miny, maxx, maxy = inner.bounds
        attempts = 0
        while len(self.targets) < count and attempts < 5000:
            attempts += 1
            x = self._rng.uniform(minx, maxx)
            y = self._rng.uniform(miny, maxy)
            if not inner.contains(Point(x, y)):
                continue
            lat, lon = frame.to_wgs84(x, y)
            if any(haversine_m(t["lat"], t["lon"], lat, lon) < 12.0 for t in self.targets):
                continue
            self.targets.append({
                "index": len(self.targets) + 1,
                "lat": lat,
                "lon": lon,
                "detected": False,
                "pending_since": None,
                "exposure_s": 0.0,
                "survivor_id": None,
                "reported_at": None,
            })

    # ------------------------------------------------------------------- loop
    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        last = loop.time()
        while self.running:
            now = loop.time()
            real_dt = min(0.5, now - last)
            last = now
            dt = real_dt * max(1.0, self.speed)
            try:
                with self._lock:
                    self._step(dt)
            except Exception as exc:
                self.bus.publish("simulation.error", {"detail": str(exc)},
                                 severity="error", source="SIMULATION", data_quality="SIMULATED")
                self.running = False
                break
            self._apply_telemetry()
            if now - self._last_publish >= 0.25:
                self._last_publish = now
                self.bus.publish("fleet.telemetry", self.telemetry.snapshot(),
                                 source="SIMULATION", data_quality="SIMULATED", persist=False)
            await asyncio.sleep(self._tick_s)

    def _step(self, dt: float) -> None:
        if self.bench:
            # Bench mode only refreshes simulated heartbeats; no targets are
            # planted and no aircraft moves before launch authority.
            return
        self.sim_time_s += dt
        cap_s = float(self.config.get("mission", "time_cap_min", default=30.0)) * 60.0
        if not self.recall_active and self.sim_time_s > cap_s:
            self._issue_recall("Mission time cap reached")
        ins = self.aircraft[self.config.search_vehicle()["callsign"]]
        self._step_search(ins, dt)
        for callsign, aircraft in self.aircraft.items():
            if aircraft["role"] == "DELIVERY":
                self._step_delivery(aircraft, dt)
        self._process_beam_queue()
        self._prune_queues()
        self._retry_unserved()
        if self.recall_active and not self._recall_issued:
            self._recall_issued = True
        if not self._completion_sent and self.sim_time_s > 5.0:
            self._check_complete()

    def _retry_unserved(self) -> None:
        """Retry unserved survivors once a delivery aircraft is parked again."""
        if not self.mission or self.recall_active or self.aborted:
            return
        if self.sim_time_s - self._last_retry < 2.0:
            return
        parked = any(
            ac["role"] == "DELIVERY" and not ac["armed"] and ac["phase"] == "PARKED"
            for ac in self.aircraft.values()
        )
        if not parked:
            return
        self._last_retry = self.sim_time_s
        try:
            self.mission.retry_unserved()
        except Exception as exc:
            self.bus.publish("allocation.retry_error", {"detail": str(exc)},
                             severity="error", source="SIMULATION", data_quality="SIMULATED")

    # ------------------------------------------------------------------ search
    def _step_search(self, ac: dict[str, Any], dt: float) -> None:
        self._drain(ac, dt, 3.2)
        if ac["phase"] == "TAKEOFF":
            if self._climb(ac, ac["search_alt"], CLIMB_RATE, dt):
                self._set_phase(ac, "SEARCH", "Reached search altitude")
                ac["mode"] = "AUTO"
        elif ac["phase"] == "SEARCH":
            if ac["recall"]:
                self._set_phase(ac, "RETURN", "Recall — leaving search area")
                ac["mode"] = "RTL"
                ac["task"] = "RETURNING"
                return
            self._detect(ac, dt)
            if ac["wp_index"] >= len(ac["waypoints"]):
                self._set_phase(ac, "RETURN", "Search lanes complete")
                ac["mode"] = "RTL"
                ac["task"] = "RETURNING"
                return
            wp = ac["waypoints"][ac["wp_index"]]
            remaining = self._fly_to(ac, wp["lat"], wp["lon"], ac["speed_cmd"], dt)
            if remaining < 1.0:
                ac["wp_index"] += 1
        elif ac["phase"] == "RETURN":
            if ac["alt"] < ac["search_alt"] - 0.1:
                self._climb(ac, ac["search_alt"], CLIMB_RATE, dt)
                ac["speed"] = 0.0
                return
            pad = self.config.get("mission", default={})
            remaining = self._fly_to(ac, float(pad["pad_lat"]), float(pad["pad_lon"]),
                                     ac["speed_cmd"], dt)
            if remaining < 2.0:
                if self._pad_free_for(ac["callsign"]):
                    self._set_phase(ac, "INS_LAND", "Landing at launch pad")
                    ac["mode"] = "LAND"
                else:
                    ac["alt"] = 8.0
                    ac["speed"] = 0.0
                    self._set_phase(ac, "HOLD_PAD", "Waiting for pad to clear")
        elif ac["phase"] == "HOLD_PAD":
            ac["speed"] = 0.0
            if self._pad_free_for(ac["callsign"]):
                self._set_phase(ac, "INS_LAND", "Pad clear — landing")
                ac["mode"] = "LAND"
        elif ac["phase"] == "INS_LAND":
            ac["mode"] = "LAND"
            ac["speed"] = 0.0
            ac["alt"] = max(0.0, ac["alt"] - LAND_RATE * dt)
            if ac["alt"] <= 0.05:
                ac["alt"] = 0.0
                ac["armed"] = False
                ac["mode"] = "STANDBY"
                ac["task"] = "STANDBY"
                ac["landed"] = True
                self._set_phase(ac, "LANDED", "Search aircraft landed and disarmed")
        elif ac["phase"] == "LANDED":
            ac["speed"] = 0.0

    def _detect(self, ac: dict[str, Any], dt: float) -> None:
        for target in self.targets:
            if target["detected"]:
                continue
            distance = haversine_m(ac["lat"], ac["lon"], target["lat"], target["lon"])
            if distance <= DETECTION_RADIUS_M:
                # Exposure accumulates while the target is inside the camera's
                # detection footprint. This keeps detection time-based rather
                # than tick-based, so accelerated simulation cannot skip a
                # survivor between two coarse steps.
                target["exposure_s"] = target.get("exposure_s", 0.0) + dt
                if target["exposure_s"] >= DETECTION_CONFIRM_S:
                    self._report_target(target)
            else:
                target["exposure_s"] = 0.0
        if (
            self._duplicate_due is None
            and self.targets
            and self.targets[0]["detected"]
            and self.sim_time_s >= self.targets[0]["reported_at"] + 8.0
        ):
            self._duplicate_due = self.sim_time_s
            first = self.targets[0]
            lat, lon = destination_m(first["lat"], first["lon"], 40.0, 3.0)
            self._emit_report(lat, lon, confidence=0.9, duplicate_of=first["survivor_id"])

    def _report_target(self, target: dict[str, Any]) -> None:
        target["detected"] = True
        target["reported_at"] = self.sim_time_s
        sigma = float(self.config.get("simulation", "detection_noise_m", default=0.6))
        lat, lon = destination_m(target["lat"], target["lon"], self._rng.uniform(0, 360), self._rng.uniform(0, sigma))
        survivor = self._emit_report(lat, lon, confidence=0.93)
        if survivor:
            target["survivor_id"] = survivor.get("survivor_id")

    def _emit_report(self, lat: float, lon: float, *, confidence: float, duplicate_of: str | None = None) -> dict[str, Any] | None:
        if not self.mission:
            return None
        envelope = {
            "type": "survivor_report",
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "confidence_mean": confidence,
            "frames_confirmed": 4,
            "geotag_uncertainty_m": 2.4,
            "model_version": "yolov8n-sim",
            "camera_calibration_version": "sim-cal-v1",
            "source_time": iso_now(),
            "simulated": True,
        }
        decision = self.mission.on_survivor_report("INS-01", envelope, data_quality="SIMULATED")
        if decision and decision.get("survivor"):
            return decision["survivor"]
        return None

    # ---------------------------------------------------------------- delivery
    def register_assignment(self, assignment: dict[str, Any]) -> None:
        callsign = assignment["callsign"]
        with self._lock:
            aircraft = self.aircraft.get(callsign)
            if not aircraft:
                return
            if assignment["assignment_id"] not in aircraft["queue"]:
                aircraft["queue"].append(assignment["assignment_id"])

    def _step_delivery(self, ac: dict[str, Any], dt: float) -> None:
        self._drain(ac, dt, 2.9)
        if ac["recall"]:
            self._recall_aircraft(ac, dt)
            return
        phase = ac["phase"]
        if phase == "PARKED":
            ac["speed"] = 0.0
            ac["alt"] = 0.0
            if ac["recall"]:
                return
            if ac["queue"] and self._pad_free_for(ac["callsign"]):
                task = self.delivery.get(ac["queue"][0])
                if task and task["state"] == "QUEUED":
                    try:
                        self.delivery.transition(task["assignment_id"], "LAUNCHING",
                                                 detail="Simulated launch")
                    except ValueError:
                        pass
                ac["armed"] = True
                ac["mode"] = "GUIDED"
                ac["task"] = "DELIVERY"
                self._set_phase(ac, "TAKEOFF", "Launching for delivery")
        elif phase == "TAKEOFF":
            if self._climb(ac, ac["transit_alt"], CLIMB_RATE, dt):
                ac["mode"] = "GUIDED"
                task = self._current_task(ac)
                if task and task["state"] == "LAUNCHING":
                    self._safe_transition(task["assignment_id"], "TRANSIT", "At transit altitude")
                self._set_phase(ac, "TRANSIT", "Transit to survivor")
        elif phase == "TRANSIT":
            task = self._current_task(ac)
            survivor = self.survivors.get(task["survivor_id"]) if task else None
            if not survivor:
                self._set_phase(ac, "RETURN", "No survivor target — returning")
                return
            target_lat, target_lon = survivor["drop_lat"], survivor["drop_lon"]
            distance = haversine_m(ac["lat"], ac["lon"], target_lat, target_lon)
            if distance < 30.0 and not self._clearance_to_target(ac, target_lat, target_lon):
                if task["state"] == "TRANSIT":
                    self._safe_transition(task["assignment_id"], "HOLD_CLEARANCE",
                                          "Traffic inside release clearance")
                self._set_phase(ac, "HOLD", "Holding for release clearance")
                ac["speed"] = 0.0
                return
            remaining = self._fly_to(ac, target_lat, target_lon, 10.0, dt)
            if remaining < 1.2:
                if task and task["state"] == "TRANSIT":
                    self._safe_transition(task["assignment_id"], "DESCENT", "Over drop point")
                self._set_phase(ac, "DESCEND", "Descending to drop height")
                ac["speed"] = 0.0
        elif phase == "HOLD":
            ac["speed"] = 0.0
            task = self._current_task(ac)
            survivor = self.survivors.get(task["survivor_id"]) if task else None
            if survivor and self._clearance_to_target(ac, survivor["drop_lat"], survivor["drop_lon"]):
                self._safe_transition(task["assignment_id"], "DESCENT", "Clearance restored")
                self._set_phase(ac, "DESCEND", "Clearance restored — descending")
        elif phase == "DESCEND":
            task = self._current_task(ac)
            survivor = self.survivors.get(task["survivor_id"]) if task else None
            if not survivor:
                self._set_phase(ac, "CLIMB", "Target lost")
                return
            target_lat, target_lon = survivor["drop_lat"], survivor["drop_lon"]
            remaining = self._fly_to(ac, target_lat, target_lon, 1.2, dt)
            ac["alt"] = max(ac["drop_alt"], ac["alt"] - DESCEND_RATE * dt)
            if ac["alt"] <= ac["drop_alt"] + 0.05 and remaining < 1.0:
                ac["alt"] = ac["drop_alt"]
                ac["speed"] = 0.0
                if task and task["state"] == "DESCENT":
                    self._safe_transition(task["assignment_id"], "HOVER_VERIFY", "Hover verification started")
                ac["hover_start"] = self.sim_time_s
                self._set_phase(ac, "HOVER", "Hover verify at drop height")
        elif phase == "HOVER":
            ac["speed"] = 0.0
            task = self._current_task(ac)
            if not task:
                self._set_phase(ac, "CLIMB", "No active task")
                return
            if self.sim_time_s - ac["hover_start"] < HOVER_VERIFY_S:
                return
            result = self.delivery.request_release(task["assignment_id"], hover_verified=True)
            if result.get("sent"):
                ac["beam_due"] = self.sim_time_s + BEAM_DELAY_S
                self._beam_queue.append((ac["beam_due"], task["assignment_id"]))
                self._set_phase(ac, "RELEASE", "Release commanded — awaiting break-beam")
            else:
                ac["hover_start"] = self.sim_time_s - (HOVER_VERIFY_S - 2.0)
        elif phase == "RELEASE":
            ac["speed"] = 0.0
        elif phase == "CLIMB":
            task = self._current_task(ac)
            if self._climb(ac, ac["transit_alt"], CLIMB_RATE, dt):
                if task and task["state"] == "CLIMB":
                    self._safe_transition(task["assignment_id"], "RETURNING", "Returning to pad")
                ac["task"] = "RETURNING"
                self._set_phase(ac, "RETURN", "Delivery complete — returning")
        elif phase == "RETURN":
            pad = self.config.get("mission", default={})
            pad_lat, pad_lon = float(pad["pad_lat"]), float(pad["pad_lon"])
            hold_lat, hold_lon = destination_m(pad_lat, pad_lon, 180.0, 14.0)
            if not self._pad_free_for(ac["callsign"]):
                ac["alt"] = max(6.0, ac["alt"])
                self._fly_to(ac, hold_lat, hold_lon, 6.0, dt)
                ac["speed"] = 0.0
                return
            remaining = self._fly_to(ac, pad_lat, pad_lon, 8.0, dt)
            if remaining < 2.0:
                ac["alt"] = max(6.0, ac["alt"])
                task = self._current_task(ac)
                if task and task["state"] == "RETURNING":
                    self._safe_transition(task["assignment_id"], "LANDING", "Landing at pad")
                self._set_phase(ac, "LANDING", "Landing at pad")
        elif phase == "LANDING":
            pad = self.config.get("mission", default={})
            self._fly_to(ac, float(pad["pad_lat"]), float(pad["pad_lon"]), 0.5, dt)
            ac["mode"] = "LAND"
            ac["speed"] = 0.0
            ac["alt"] = max(0.0, ac["alt"] - LAND_RATE * dt)
            if ac["alt"] <= 0.05:
                ac["alt"] = 0.0
                ac["armed"] = False
                ac["mode"] = "STANDBY"
                ac["task"] = "STANDBY"
                task = self._current_task(ac)
                if task and task["state"] == "LANDING":
                    self._safe_transition(task["assignment_id"], "STANDBY", "Landed and disarmed")
                self._set_phase(ac, "PARKED", "Landed and disarmed")
        elif phase == "LOITER":
            ac["speed"] = 0.0

    def _current_task(self, ac: dict[str, Any]) -> dict[str, Any] | None:
        if not ac["queue"]:
            return None
        return self.delivery.get(ac["queue"][0])

    def _clearance_to_target(self, ac: dict[str, Any], lat: float, lon: float) -> bool:
        clearance = float(self.config.get("mission", "descent_clearance_m", default=20.0))
        for other in self.telemetry.vehicles.values():
            if other["callsign"] == ac["callsign"]:
                continue
            if other.get("lat") is None:
                return False
            if haversine_m(other["lat"], other["lon"], lat, lon) < clearance:
                return False
        return True

    def _pad_free_for(self, callsign: str) -> bool:
        mission = self.config.get("mission", default={})
        pad_lat, pad_lon = float(mission["pad_lat"]), float(mission["pad_lon"])
        for callsign_other, other in self.aircraft.items():
            if callsign_other == callsign or not other["armed"]:
                continue
            if haversine_m(other["lat"], other["lon"], pad_lat, pad_lon) <= 8.0 and other["alt"] < 2.0:
                return False
        return True

    def _recall_aircraft(self, ac: dict[str, Any], dt: float) -> None:
        phase = ac["phase"]
        if phase == "PARKED":
            for assignment_id in list(ac["queue"]):
                task = self.delivery.get(assignment_id)
                if task and task["state"] in {"QUEUED", "LAUNCHING", "TRANSIT", "HOLD_CLEARANCE",
                                              "DESCENT", "HOVER_VERIFY"}:
                    self._safe_transition(assignment_id, "ABORTED", "Emergency recall on ground")
                    self._safe_transition(assignment_id, "STANDBY", "Aborted on ground")
            ac["queue"] = []
            ac["speed"] = 0.0
            ac["alt"] = 0.0
            ac["armed"] = False
            ac["mode"] = "STANDBY"
            ac["task"] = "STANDBY"
            return
        task = self._current_task(ac)
        for assignment_id in list(ac["queue"][1:]):
            queued = self.delivery.get(assignment_id)
            if queued and queued["state"] in {"QUEUED", "LAUNCHING"}:
                self._safe_transition(assignment_id, "ABORTED", "Emergency recall — queued task")
                self._safe_transition(assignment_id, "STANDBY", "Aborted while queued")
            ac["queue"] = [item for item in ac["queue"] if item != assignment_id]
        if task and task["state"] in {"QUEUED", "LAUNCHING", "TRANSIT", "HOLD_CLEARANCE",
                                      "DESCENT", "HOVER_VERIFY"}:
            self._safe_transition(task["assignment_id"], "ABORTED", "Emergency recall")
            self._safe_transition(task["assignment_id"], "RETURNING", "Emergency recall")
        if phase in {"TAKEOFF", "TRANSIT", "HOLD", "DESCEND", "HOVER"}:
            ac["task"] = "EMERGENCY"
            ac["phase"] = "CLIMB"
            ac["mode"] = "GUIDED"
            phase = "CLIMB"
        if phase == "CLIMB":
            if self._climb(ac, ac["transit_alt"], CLIMB_RATE, dt):
                if task and task["state"] == "CLIMB":
                    self._safe_transition(task["assignment_id"], "RETURNING", "Emergency recall")
                self._set_phase(ac, "RETURN", "Recall — returning to pad")
        elif phase == "RETURN":
            pad = self.config.get("mission", default={})
            pad_lat, pad_lon = float(pad["pad_lat"]), float(pad["pad_lon"])
            hold_lat, hold_lon = destination_m(pad_lat, pad_lon, 180.0, 14.0)
            if not self._pad_free_for(ac["callsign"]):
                ac["alt"] = max(6.0, ac["alt"])
                self._fly_to(ac, hold_lat, hold_lon, 6.0, dt)
                ac["speed"] = 0.0
                return
            remaining = self._fly_to(ac, pad_lat, pad_lon, 10.0, dt)
            if remaining < 2.0:
                if task and task["state"] == "RETURNING":
                    self._safe_transition(task["assignment_id"], "LANDING", "Recall landing")
                self._set_phase(ac, "LANDING", "Recall landing")
                ac["mode"] = "LAND"
        elif phase == "LANDING":
            pad = self.config.get("mission", default={})
            self._fly_to(ac, float(pad["pad_lat"]), float(pad["pad_lon"]), 0.5, dt)
            ac["mode"] = "LAND"
            ac["speed"] = 0.0
            ac["alt"] = max(0.0, ac["alt"] - LAND_RATE * dt)
            if ac["alt"] <= 0.05:
                ac["alt"] = 0.0
                ac["armed"] = False
                ac["mode"] = "STANDBY"
                ac["task"] = "STANDBY"
                if task and task["state"] == "LANDING":
                    self._safe_transition(task["assignment_id"], "STANDBY", "Recall landing complete")
                ac["queue"] = []
                self._set_phase(ac, "PARKED", "Recall landing complete")

    def _process_beam_queue(self) -> None:
        due = [item for item in self._beam_queue if item[0] <= self.sim_time_s]
        self._beam_queue = [item for item in self._beam_queue if item[0] > self.sim_time_s]
        for _, assignment_id in due:
            task = self.delivery.get(assignment_id)
            if not task or task["state"] != "RELEASE_SENT":
                continue
            probability = float(self.config.get("simulation", "breakbeam_failure_probability", default=0.08))
            forced = self.forced_beam.pop(task["callsign"], None)
            if forced:
                beam = forced
            elif self._rng.random() < probability:
                beam = "NO_BEAM" if self._rng.random() < 0.7 else "SENSOR_FAULT"
            else:
                beam = "CONFIRMED"
            self.delivery.apply_beam_result(assignment_id, beam)
            aircraft = self.aircraft.get(task["callsign"])
            if aircraft:
                self._safe_transition(assignment_id, "CLIMB", f"Beam {beam}")
                aircraft["phase"] = "CLIMB"
                aircraft["task"] = "RETURNING"
                self._publish_phase(aircraft, "CLIMB", f"Break-beam {beam}")

    def _prune_queues(self) -> None:
        for ac in self.aircraft.values():
            if not ac["queue"]:
                continue
            kept = []
            for assignment_id in ac["queue"]:
                task = self.delivery.get(assignment_id)
                if task and task["state"] in {"STANDBY", "ABORTED"}:
                    continue
                kept.append(assignment_id)
            ac["queue"] = kept

    # ---------------------------------------------------------------- helpers
    def _fly_to(self, ac: dict[str, Any], lat: float, lon: float, speed: float, dt: float) -> float:
        distance = haversine_m(ac["lat"], ac["lon"], lat, lon)
        if distance <= 0.01 or dt <= 0:
            ac["speed"] = 0.0
            return distance
        step = min(distance, speed * dt)
        bearing = bearing_deg(ac["lat"], ac["lon"], lat, lon)
        ac["lat"], ac["lon"] = destination_m(ac["lat"], ac["lon"], bearing, step)
        ac["heading"] = bearing
        ac["speed"] = step / dt
        return max(0.0, distance - step)

    def _climb(self, ac: dict[str, Any], target_alt: float, rate: float, dt: float) -> bool:
        if ac["alt"] >= target_alt - 0.05:
            ac["alt"] = target_alt
            ac["speed"] = 0.0
            return True
        ac["alt"] = min(target_alt, ac["alt"] + rate * dt)
        return ac["alt"] >= target_alt - 0.05

    def _drain(self, ac: dict[str, Any], dt: float, rate_pct_per_min: float) -> None:
        if ac["alt"] <= 0.01 and not ac["armed"]:
            return
        ac["battery"] = max(0.0, ac["battery"] - rate_pct_per_min * dt / 60.0)

    def _set_phase(self, ac: dict[str, Any], phase: str, detail: str) -> None:
        if ac["phase"] == phase:
            return
        ac["phase"] = phase
        ac["phase_since"] = self.sim_time_s
        self._publish_phase(ac, phase, detail)

    def _publish_phase(self, ac: dict[str, Any], phase: str, detail: str) -> None:
        self.bus.publish(
            "flight.phase",
            {"callsign": ac["callsign"], "phase": phase, "detail": detail,
             "sim_time_s": round(self.sim_time_s, 1)},
            source=ac["callsign"], data_quality="SIMULATED",
        )

    def _safe_transition(self, assignment_id: str, state: str, detail: str) -> None:
        try:
            self.delivery.transition(assignment_id, state, detail=detail)
        except (ValueError, KeyError):
            pass

    # ------------------------------------------------------------------ recall
    def recall(self, reason: str) -> None:
        self._issue_recall(reason)

    def _issue_recall(self, reason: str) -> None:
        if self.recall_active:
            return
        self.recall_active = True
        self.recall_reason = reason
        for ac in self.aircraft.values():
            ac["recall"] = True
            if ac["role"] == "SEARCH":
                ac["task"] = "RETURNING"
                ac["mode"] = "RTL"
                if ac["phase"] in {"SEARCH", "TAKEOFF"}:
                    self._set_phase(ac, "RETURN", "Emergency recall")
        self.bus.publish("simulation.recall", {"reason": reason},
                         severity="critical", source="SIMULATION", data_quality="SIMULATED")

    def abort(self, reason: str) -> None:
        self.aborted = True
        for ac in self.aircraft.values():
            if ac["armed"]:
                ac["mode"] = "LOITER"
                ac["phase"] = "LOITER"
                ac["speed"] = 0.0
                ac["task"] = "ABORTED"
        self._apply_telemetry()
        self.bus.publish("simulation.abort", {"reason": reason},
                         severity="critical", source="SIMULATION", data_quality="SIMULATED")
        self.stop()

    # -------------------------------------------------------------- completion
    def _check_complete(self) -> None:
        if self.bench:
            return
        all_landed = all(not ac["armed"] and ac["alt"] <= 0.05 for ac in self.aircraft.values())
        if not all_landed:
            return
        active = [
            task for task in self.delivery.tasks.values()
            if task["state"] not in {"STANDBY", "ABORTED", "CONFIRMED", "UNKNOWN"}
        ]
        if active:
            return
        self._completion_sent = True
        outcome = "EMERGENCY_RECALL_COMPLETE" if self.recall_active else "SIMULATION_COMPLETE"
        metrics = {
            "outcome": outcome,
            "sim_time_s": round(self.sim_time_s, 1),
            "survivors_planted": len(self.targets),
            "survivors_detected": sum(1 for t in self.targets if t["detected"]),
            "recall_reason": self.recall_reason,
            "label": "SIMULATED — generated by the engineering simulator",
        }
        if self.mission:
            self.mission.on_simulation_complete(metrics)
        # Keep publishing disarmed heartbeats at the pad after the simulated
        # mission ends, so the GCS does not misreport the fleet as "link lost"
        # merely because the scenario finished. No movement, targets or
        # deliveries are generated in this bench state.
        self.bench = True

    # ------------------------------------------------------------------ inject
    def inject(self, fault: str, callsign: str | None = None, value: Any = None) -> dict[str, Any]:
        """Inject a labelled simulation fault for testing. Never available in REAL."""
        if fault == "link_loss":
            if not callsign or callsign not in self.aircraft:
                raise ValueError("link_loss requires a known callsign")
            self.link_lost.add(callsign)
            state = self.telemetry.vehicles[callsign]
            state.update(connected=False, stale=True, link="LOST", data_quality="LOST",
                         identity_valid=False, error="Injected simulation link loss")
        elif fault == "restore_link":
            if not callsign:
                raise ValueError("restore_link requires a callsign")
            self.link_lost.discard(callsign)
            self.telemetry.update_sim_state(callsign)
        elif fault == "battery":
            if not callsign or callsign not in self.aircraft:
                raise ValueError("battery requires a known callsign")
            percent = float(value if value is not None else 30.0)
            self.aircraft[callsign]["battery"] = max(0.0, min(100.0, percent))
        elif fault == "breakbeam":
            if not callsign:
                raise ValueError("breakbeam requires a callsign")
            result = str(value or "NO_BEAM").upper()
            if result not in {"CONFIRMED", "NO_BEAM", "SENSOR_FAULT", "UNKNOWN"}:
                raise ValueError("Unsupported break-beam result")
            self.forced_beam[callsign] = result
        elif fault == "video_loss":
            if not callsign:
                raise ValueError("video_loss requires a callsign")
            self.mission.video.inject(callsign, "loss")
        else:
            raise ValueError(f"Unsupported simulation fault {fault}")
        self.bus.publish("simulation.fault", {"fault": fault, "callsign": callsign, "value": value},
                         severity="warning", source="SIMULATION", data_quality="SIMULATED")
        return {"fault": fault, "callsign": callsign, "value": value}

    # -------------------------------------------------------------- telemetry
    def _apply_telemetry(self) -> None:
        for ac in self.aircraft.values():
            if ac["callsign"] in self.link_lost:
                continue
            self.telemetry.update_sim_state(
                ac["callsign"],
                lat=round(ac["lat"], 7),
                lon=round(ac["lon"], 7),
                alt_rel_m=round(ac["alt"], 2),
                agl_m=round(ac["alt"], 2),
                groundspeed_ms=round(ac["speed"], 2),
                vertical_speed_ms=0.0,
                heading_deg=round(ac["heading"], 1),
                battery_pct=round(ac["battery"], 1),
                battery_voltage=round(22.2 + 3.0 * ac["battery"] / 100.0, 2),
                mode=ac["mode"],
                armed=ac["armed"],
                task=ac["task"],
                queue_depth=len(ac["queue"]),
                kits_free=self.ledger.free_count(ac["callsign"]),
                link_quality=98,
                rssi=210,
                gps_fix=3,
                gps_sats=19,
                ekf_ok=True,
                rangefinder_m=round(ac["alt"], 2) if ac["role"] == "DELIVERY" else None,
                failsafe="RECALL" if self.recall_active else None,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "bench": self.bench,
                "mode": "SIMULATION",
                "speed": self.speed,
                "sim_time_s": round(self.sim_time_s, 1),
                "recall": self.recall_active,
                "recall_reason": self.recall_reason,
                "aircraft": [
                    {
                        "callsign": ac["callsign"],
                        "phase": ac["phase"],
                        "armed": ac["armed"],
                        "mode": ac["mode"],
                        "alt_m": round(ac["alt"], 2),
                        "battery_pct": round(ac["battery"], 1),
                        "task": ac["task"],
                        "queue_depth": len(ac["queue"]),
                    }
                    for ac in self.aircraft.values()
                ],
                "targets": {
                    "planted": len(self.targets),
                    "detected": sum(1 for t in self.targets if t["detected"]),
                },
                "label": "SIMULATED",
            }
