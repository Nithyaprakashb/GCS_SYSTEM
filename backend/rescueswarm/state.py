"""Application composition root: one place that wires every service."""

from __future__ import annotations

import asyncio
from typing import Any

from .allocation import TaskAllocator
from .config import APP_VERSION, Config, data_dir
from .copilot import Copilot
from .db import Database
from .delivery import DeliveryEngine
from .events import EventBus
from .evidence import EvidenceStore
from .ledger import KitLedger
from .map_tiles import MapTileService
from .mission_engine import MissionEngine
from .preflight import PreflightEngine
from .replay import ReplayEngine
from .reporting import Reporter
from .safety import SafetyEngine
from .simulation import SimulationEngine
from .survivors import SurvivorRegistry
from .system import SystemService
from .telemetry import TelemetryManager
from .upload import MissionUploader
from .util import iso_now
from .video import VideoManager


class AppState:
    def __init__(self) -> None:
        self.config = Config()
        self.db = Database()
        self.evidence = EvidenceStore(data_dir() / "Missions")
        self.bus = EventBus(self.db, self.evidence)
        self.telemetry = TelemetryManager(self.config, self.db, self.bus)
        self.survivors = SurvivorRegistry(self.config, self.db, self.bus)
        self.ledger = KitLedger(self.config, self.db, self.bus)
        self.allocator = TaskAllocator(self.config, self.db, self.bus, self.ledger, self.survivors)
        self.safety = SafetyEngine(self.config, self.bus, self.ledger)
        self.delivery = DeliveryEngine(self.config, self.db, self.bus, self.ledger,
                                       self.survivors, self.safety, self.telemetry)
        self.delivery.allocator = self.allocator
        self.video = VideoManager(self.config, self.db, self.bus, self.telemetry, self.evidence)
        self.preflight = PreflightEngine(self.config, self.db, self.bus, self.telemetry,
                                         self.ledger, self.evidence)
        self.tiles = MapTileService(data_dir() / "map_tiles")
        self.uploader = MissionUploader(self.config, self.telemetry)
        self.mission = MissionEngine(
            self.config, self.db, self.bus, self.evidence, self.telemetry, self.video,
            self.survivors, self.ledger, self.allocator, self.safety, self.delivery,
            self.preflight, self.uploader,
        )
        self.simulation = SimulationEngine(
            self.config, self.db, self.bus, self.telemetry, self.survivors,
            self.ledger, self.delivery, self.safety,
        )
        self.simulation.mission = self.mission
        self.mission.simulation = self.simulation
        self.reporter = Reporter(self.db, self.evidence)
        self.mission.reporter = self.reporter
        self.replay = ReplayEngine(self.db)
        self.copilot = Copilot(self.config)
        self.system = SystemService(self.config, self.db, self.evidence, self.video)
        self.telemetry.project_event_handler = self._on_project_event
        self.telemetry.sim_command_handler = self._on_sim_command
        self.telemetry.command_settled_handler = self._on_command_settled
        self.started_at = iso_now()
        self._shutdown_done = False

    # --------------------------------------------------------------- lifecycle
    async def startup(self) -> None:
        self.bus.bind_loop(asyncio.get_running_loop())
        self.bus.start_persistence()
        await self.telemetry.start_monitors()

    async def shutdown(self) -> dict[str, Any]:
        """Close every child service this process owns. Calling it twice is harmless.

        Order matters: the simulator and the capture/recorder children are stopped
        before the event writer and the database, so nothing can queue work against a
        closed handle. Onboard autopilot protections are parameters on the aircraft;
        stopping the ground station cannot and does not touch them.
        """
        if self._shutdown_done:
            return {"already_stopped": True}
        self.simulation.stop()
        video = self.video.stop()
        self.telemetry.disconnect()
        # Drain queued evidence before the database closes, then stop the writer.
        drained = self.bus.flush_pending(2.0)
        stats = self.bus.persistence_stats()
        self.bus.stop_persistence(2.0)
        self.db.close()
        self._shutdown_done = True
        return {
            "already_stopped": False,
            "capture_processes_closed": video.get("capture_processes", 0),
            "recorders_closed": video.get("recorders_closed", 0),
            "capture_threads_joined": video.get("threads_joined", 0),
            "event_queue_drained": drained,
            "events_written": stats.get("written", 0),
            "event_write_errors": stats.get("errors", 0),
            "onboard_parameters_touched": 0,
        }

    # ------------------------------------------------------------------- modes
    async def set_mode(self, mode: str, operator: str = "operator") -> dict[str, Any]:
        if self.mission.phase == "EXECUTING":
            raise RuntimeError("Cannot change system mode during mission execution")
        mode = mode.upper()
        if mode not in {"SIMULATION", "REAL"}:
            raise ValueError("Mode must be SIMULATION or REAL")
        if self.telemetry.connected:
            self.telemetry.disconnect()
        self.simulation.stop()
        self.video.stop()
        self.mission.set_mode(mode)
        self.bus.publish("operator.action",
                         {"action": "SET_MODE", "mode": mode, "operator": operator},
                         source="OPERATOR", data_quality="SYSTEM")
        return {"mode": mode, "telemetry": self.telemetry.mode}

    async def connect_fleet(self, mode: str, operator: str = "operator") -> dict[str, Any]:
        await self.set_mode(mode, operator)
        result = await self.telemetry.connect(mode)
        if mode.upper() == "SIMULATION":
            self.simulation.start_bench()
            self.video.start("SIMULATION")
        else:
            self.simulation.stop()
        self.bus.publish("operator.action",
                         {"action": "FLEET_CONNECT", "mode": mode, "operator": operator},
                         source="OPERATOR", data_quality=result.get("mode", "SYSTEM"))
        return {
            "connection": result,
            "fleet": self.telemetry.snapshot(),
            "video": self.video.health(),
        }

    async def disconnect_fleet(self) -> dict[str, Any]:
        if self.mission.phase == "EXECUTING":
            raise RuntimeError("Disconnecting the GCS does not stop the aircraft; use EMERGENCY RECALL")
        self.telemetry.disconnect()
        self.simulation.stop()
        self.video.stop()
        return {"fleet": self.telemetry.snapshot(), "video": self.video.health()}

    # ------------------------------------------------------------------ events
    def _on_project_event(self, callsign: str, envelope: dict[str, Any]) -> None:
        self.mission.on_project_event(callsign, envelope)

    def _on_sim_command(self, callsign: str, command: int, params: list[float]) -> bool:
        # The simulator accepts commands so the transaction/ACK path is exercised.
        return True

    def _on_command_settled(self, tx_id: str) -> None:
        """Feed a terminal command transaction into the delivery state machine.

        This runs on the MAVLink reader thread in REAL mode, so it must stay short
        and must never raise into the link loop.
        """
        try:
            self.delivery.settle_release_transaction(tx_id)
        except Exception as exc:
            self.bus.publish(
                "system.error",
                {"detail": f"Release settlement failed for {tx_id}: {exc}"},
                severity="critical", source="GCS",
                data_quality="SIMULATED" if self.telemetry.mode == "SIMULATION" else "LIVE",
            )

    # ------------------------------------------------------------------- views
    def ui_snapshot(self) -> dict[str, Any]:
        aoi = self.mission.aoi
        return {
            "app": {
                "version": APP_VERSION,
                "started_at": self.started_at,
                "mode": self.mission.mode,
                "telemetry_mode": self.telemetry.mode,
                "offline": True,
                "data_dir": str(data_dir()),
            },
            "mission": self.mission.status(),
            "fleet": self.telemetry.snapshot(),
            "safety": self.safety.evaluate_fleet(self.telemetry.vehicles, aoi),
            "survivors": self.survivors.snapshot(),
            "kits": self.ledger.snapshot(),
            "allocations": self.allocator.snapshot(),
            "deliveries": self.delivery.snapshot(),
            "video": self.video.health(),
            "preflight": {
                "manual": self.preflight.manual_items(),
                "last": self.preflight.last_report,
            },
            "events": self.bus.recent(80),
            "generated_at": iso_now(),
        }
