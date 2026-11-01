"""Fleet telemetry registry, identity validation, command transactions and staleness."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Config
from .db import Database
from .events import EventBus
from .util import clamp, iso_now, new_id, safe_round
from .mavlink import MavlinkTransport, TransportError

HEARTBEAT_ARMED_FLAG = 128
MAV_AUTOPILOT_ARDUPILOTMEGA = 3
MAV_TYPE_QUADROTOR = 2
MAV_TYPE_HEXAROTOR = 13

ARDUPILOT_MODES = {
    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER",
    6: "RTL", 7: "CIRCLE", 9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP",
    15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW", 19: "AVOID_ADSB",
    20: "GUIDED_NOGPS", 21: "SMART_RTL", 22: "FLOWHOLD", 23: "FOLLOW", 24: "ZIGZAG",
    25: "SYSTEMID", 26: "AUTOROTATE", 27: "AUTO_RTL",
}

MAV_CMD_DO_SET_MODE = 176
MAV_CMD_COMPONENT_ARM_DISARM = 400
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
MAV_CMD_DO_REPOSITION = 192
MAV_CMD_DO_SET_SERVO = 183


# Transaction states.
#   CREATED -> SENT -> ACKNOWLEDGED -> VERIFIED        (command accepted AND vehicle
#                                                       state reached the expected condition)
#                     -> REJECTED / TIMEOUT            (command did not take effect)
#                     -> ACK_UNVERIFIED                (autopilot accepted the command but the
#                                                       expected physical state was not seen)
# ACKNOWLEDGED is never treated as proof that the physical action finished.
TERMINAL_TX_STATES = {"VERIFIED", "REJECTED", "TIMEOUT", "ACK_UNVERIFIED"}

# ACKNOWLEDGED is deliberately NOT terminal: the sweep has to keep watching it so it can
# become VERIFIED or ACK_UNVERIFIED. It is, however, a settling moment — the delivery engine
# must react to the acceptance of a release command the instant the ACK arrives, because a
# servo command has no vehicle-state proof available to the GCS. Conflating those two ideas
# is what used to freeze every acknowledged command in a state it could never leave.
SETTLING_TX_STATES = TERMINAL_TX_STATES | {"ACKNOWLEDGED"}


@dataclass
class CommandTransaction:
    tx_id: str
    callsign: str
    command: int
    label: str
    params: list[float]
    state: str = "CREATED"
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)
    attempts: int = 0
    ack_result: int | None = None
    detail: str = ""
    simulated: bool = False
    # True only once a frame was actually written to a real MAVLink transport. This is what
    # separates "the GCS never let the command out" from "the command went out and nothing
    # came back", and therefore what separates a kit that is provably still in the magazine
    # from one whose fate is unknown. Simulation leaves it False: no frame left the station.
    transmitted: bool = False
    expectation: dict[str, Any] | None = None
    ack_at: str | None = None
    verified_at: str | None = None
    timeout_at: float | None = None
    verify_deadline: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tx_id": self.tx_id,
            "callsign": self.callsign,
            "command": self.command,
            "label": self.label,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attempts": self.attempts,
            "ack_result": self.ack_result,
            "detail": self.detail,
            "simulated": self.simulated,
            "transmitted": self.transmitted,
            "expectation": self.expectation,
            "ack_at": self.ack_at,
            "verified_at": self.verified_at,
        }


class TelemetryManager:
    def __init__(self, config: Config, db: Database, bus: EventBus) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self._lock = threading.RLock()
        self.vehicles: dict[str, dict[str, Any]] = {}
        self.transports: dict[str, MavlinkTransport] = {}
        self.shared_transport: MavlinkTransport | None = None
        self.mode = "SIMULATION"
        self.connected = False
        self.topology = config.get("telemetry", "topology", default="shared")
        self.topology_status = "UNVERIFIED"
        self._last_unexpected: dict[int, float] = {}
        self._last_endpoint_mismatch: dict[str, float] = {}
        self._monitor_task: asyncio.Task | None = None
        self._persist_task: asyncio.Task | None = None
        self._last_persist: dict[str, float] = {}
        self._last_broadcast = 0.0
        self._transactions: dict[str, CommandTransaction] = {}
        self._tx_timeout_task: asyncio.Task | None = None
        self.sim_command_handler: Callable[[str, int, list[float]], bool] | None = None
        self.command_settled_handler: Callable[[str], None] | None = None
        self.project_event_handler: Callable[[str, dict[str, Any]], None] | None = None
        self.mission_message_handler: Callable[[str, Any], None] | None = None
        self.param_message_handler: Callable[[str, Any], None] | None = None
        self._init_vehicles()

    # ------------------------------------------------------------------ setup
    def _init_vehicles(self) -> None:
        for profile in self.config.fleet:
            self.vehicles[profile["callsign"]] = {
                "callsign": profile["callsign"],
                "sysid": profile["sysid"],
                "role": profile["role"],
                "vehicle_type": profile["vehicle_type"],
                "frame": profile.get("frame"),
                "connected": False,
                "link": "OFFLINE",
                "identity_valid": False,
                "identity_note": "No heartbeat received",
                "mode": "—",
                "custom_mode": None,
                "armed": False,
                "lat": None,
                "lon": None,
                "alt_msl_m": None,
                "alt_rel_m": None,
                "agl_m": None,
                "groundspeed_ms": None,
                "vertical_speed_ms": None,
                "heading_deg": None,
                "battery_pct": None,
                "battery_voltage": None,
                "battery_remaining": None,
                "gps_fix": 0,
                "gps_sats": 0,
                "gps_eph": None,
                "ekf_ok": None,
                "ekf_variance": None,
                "rangefinder_m": None,
                "link_quality": None,
                "rssi": None,
                "last_heartbeat": None,
                "last_update": None,
                "heartbeat_age_s": None,
                "stale": True,
                "data_quality": "UNAVAILABLE",
                "source": "NONE",
                "task": "STANDBY",
                "mission_seq": None,
                "home_lat": self.config.get("mission", "pad_lat"),
                "home_lon": self.config.get("mission", "pad_lon"),
                "failsafe": None,
                "statustext": "",
                "queue_depth": 0,
                "kits_free": int(profile.get("kits", 0)),
                "drop_height_m": profile.get("drop_height_m"),
                "transit_alt_m": profile.get("transit_alt_m"),
                "search_alt_m": profile.get("search_alt_m"),
                "error": None,
                "last_seen_mono": 0.0,
                "source_time": None,
            }

    def reset(self) -> None:
        self.disconnect()
        self._init_vehicles()
        self._transactions.clear()

    def _abandon_pending_transactions(self, reason: str) -> None:
        """No transaction may survive a link change unresolved.

        An accepted command and an unanswered one must not collapse into the same label:
        TIMEOUT on an ACK the vehicle really sent would tell the operator the command never
        arrived, which is its own kind of false evidence.
        """
        for tx in list(self._transactions.values()):
            if tx.state in TERMINAL_TX_STATES:
                continue
            if tx.ack_at:
                tx.state = "ACK_UNVERIFIED"
                tx.detail = f"{reason}; the acceptance was received but the physical result was never seen"
            else:
                tx.state = "TIMEOUT"
                tx.detail = reason
            tx.updated_at = iso_now()
            tx.timeout_at = None
            tx.verify_deadline = None
            self._settle(tx, "command.abandoned", severity="warning")

    def _reset_vehicle_records(self) -> None:
        """Wipe cached telemetry so records seeded in one mode cannot survive into the other.

        Without this, a simulated heartbeat leaves connected=True / identity_valid=True
        behind after switching to REAL and the pre-launch freshness gate passes with zero
        radio traffic.
        """
        self._init_vehicles()

    # ---------------------------------------------------------------- connect
    async def connect(self, mode: str) -> dict[str, Any]:
        mode = mode.upper()
        if mode not in {"SIMULATION", "REAL"}:
            raise ValueError("Mode must be SIMULATION or REAL")
        self.disconnect()
        self._abandon_pending_transactions("Link mode changed before the vehicle answered")
        self._init_vehicles()
        self.mode = mode
        if mode == "SIMULATION":
            self.connected = True
            self.topology_status = "VERIFIED_SIMULATION"
            now = time.monotonic()
            for callsign, state in self.vehicles.items():
                state.update(
                    connected=True, link="SIM", source="SIMULATED", data_quality="SIMULATED",
                    stale=False, last_seen_mono=now, last_heartbeat=iso_now(), last_update=iso_now(),
                    identity_valid=True, identity_note="Simulated identity verified",
                    battery_pct=100.0, battery_voltage=25.2, gps_fix=4, gps_sats=18,
                    ekf_ok=True, mode="STANDBY", armed=False,
                    lat=self.config.get("mission", "pad_lat"),
                    lon=self.config.get("mission", "pad_lon"),
                    alt_rel_m=0.0, agl_m=0.0, groundspeed_ms=0.0, heading_deg=0.0,
                )
            self.bus.publish("fleet.connection", {"connected": True, "topology": "SIMULATED"},
                             source="TELEMETRY", data_quality="SIMULATED")
            return {"connected": True, "mode": mode, "topology": "SIMULATED"}

        if mode == "REAL":
            self.topology = self.config.get("telemetry", "topology", default="shared")
            endpoints: list[tuple[str, str | None]] = []
            if self.topology == "shared":
                endpoint = self.config.get("telemetry", "shared_endpoint")
                if not endpoint:
                    raise TransportError("No RFD868x ground modem endpoint is configured")
                endpoints.append((endpoint, None))
            else:
                for callsign in self.config.callsigns:
                    endpoint = self.config.get("telemetry", "independent_endpoints", default={}).get(callsign)
                    if not endpoint:
                        raise TransportError(f"No telemetry endpoint configured for {callsign}")
                    endpoints.append((endpoint, callsign))
            try:
                for endpoint, callsign in endpoints:
                    label = callsign or "shared"
                    transport = MavlinkTransport(
                        endpoint,
                        baud=int(self.config.get("telemetry", "baud", default=57600)),
                        source_system=int(self.config.get("telemetry", "gcs_sysid", default=255)),
                        on_message=lambda msg, cs=callsign: self._on_mavlink(cs, msg),
                        on_state=lambda name, state: self.bus.publish(
                            "fleet.connection", {"transport": name, "state": state},
                            source="TELEMETRY", data_quality="LIVE",
                        ),
                        name=label,
                    )
                    transport.start()
                    if callsign:
                        self.transports[callsign] = transport
                    elif self.shared_transport is None:
                        self.shared_transport = transport
                    else:
                        self.transports["shared-2"] = transport
                self.connected = True
            except Exception as exc:
                self.disconnect()
                raise TransportError(str(exc)) from exc
            self.topology_status = "UNVERIFIED"
            self.bus.publish("fleet.connection", {"connected": True, "topology": self.topology},
                             source="TELEMETRY", data_quality="LIVE")
            return {"connected": True, "mode": mode, "topology": self.topology,
                    "status": "Radio topology unverified — commissioning required"}

        return {"connected": False}

    def disconnect(self) -> None:
        for transport in list(self.transports.values()):
            transport.close()
        self.transports.clear()
        if self.shared_transport:
            self.shared_transport.close()
            self.shared_transport = None
        for state in self.vehicles.values():
            state["connected"] = False
            state["stale"] = True
            state["link"] = "OFFLINE"
            state["identity_valid"] = False
            state["identity_note"] = "Link closed"
            state["data_quality"] = "UNAVAILABLE"
            state["source"] = "NONE"
        if self.connected:
            self.bus.publish("fleet.connection", {"connected": False},
                             source="TELEMETRY", data_quality="LIVE")
        self.connected = False

    # ------------------------------------------------------------ simulation
    def sim_heartbeat(self, callsign: str, **fields: Any) -> None:
        with self._lock:
            state = self.vehicles[callsign]
            state["last_seen_mono"] = time.monotonic()
            state["last_heartbeat"] = iso_now()
            state["last_update"] = iso_now()
            state["connected"] = True
            state["stale"] = False
            state["link"] = "SIM"
            state["source"] = "SIMULATED"
            state["data_quality"] = "SIMULATED"
            state["identity_valid"] = True
            if fields:
                state.update({key: value for key, value in fields.items() if value is not None or key in {"agl_m", "lat", "lon"}})

    def update_sim_state(self, callsign: str, **fields: Any) -> None:
        self.sim_heartbeat(callsign, **fields)

    # ------------------------------------------------------------------ MAVLink
    def _on_mavlink(self, endpoint_callsign: str | None, msg: Any) -> None:
        try:
            msg_type = msg.get_type()
            sysid = int(getattr(msg, "get_srcSystem", lambda: 0)())
            callsign = self.config.sysid_map.get(sysid)
            # Independent-topology commissioning check: a modem wired to one aircraft must
            # not be the path for another one, or the operator believes they have two links
            # while both commands actually reach the same vehicle.
            if endpoint_callsign and callsign and callsign != endpoint_callsign:
                now = time.monotonic()
                if now - self._last_endpoint_mismatch.get(callsign, 0) > 10:
                    self._last_endpoint_mismatch[callsign] = now
                    self.bus.publish(
                        "safety.interlock",
                        {"rule_id": "ENDPOINT_MISMATCH", "callsign": callsign,
                         "endpoint": endpoint_callsign, "sysid": sysid,
                         "detail": f"SysID {sysid} ({callsign}) answered on the {endpoint_callsign} "
                                   f"modem; independent-link wiring is wrong and this message was ignored"},
                        severity="critical", source="TELEMETRY", data_quality="LIVE",
                    )
                return
            # Deliberately no blanket "if endpoint_callsign: return" here. On an independent
            # modem every message — heartbeat, position, COMMAND_ACK, mission and param
            # replies — arrives with the endpoint's own vehicle as the source, and dropping
            # all of it made independent topology silently dead: the aircraft answered, the
            # GCS reported it offline and refused every command. The wiring interlock is the
            # mismatch check above, not a filter on the whole transport.
            if msg_type == "HEARTBEAT":
                self._on_heartbeat(sysid, callsign, msg)
                return
            if not callsign:
                return
            state = self.vehicles[callsign]
            now = time.monotonic()
            state["last_seen_mono"] = now
            state["last_update"] = iso_now()
            state["source_time"] = iso_now()
            state["source"] = "MAVLINK"
            state["data_quality"] = "LIVE"
            state["connected"] = True
            state["stale"] = False
            if msg_type == "GLOBAL_POSITION_INT":
                state["lat"] = msg.lat / 1e7
                state["lon"] = msg.lon / 1e7
                state["alt_msl_m"] = msg.alt / 1000.0
                state["alt_rel_m"] = msg.relative_alt / 1000.0
                state["vertical_speed_ms"] = -msg.vz / 100.0
                state["groundspeed_ms"] = (msg.vx ** 2 + msg.vy ** 2) ** 0.5 / 100.0
                if msg.hdg != 65535:
                    state["heading_deg"] = msg.hdg / 100.0
            elif msg_type == "GPS_RAW_INT":
                state["gps_fix"] = msg.fix_type
                state["gps_sats"] = msg.satellites_visible
                state["gps_eph"] = msg.eph / 100.0 if msg.eph != 65535 else None
            elif msg_type == "SYS_STATUS":
                if msg.voltage_battery not in (0, 65535):
                    state["battery_voltage"] = msg.voltage_battery / 1000.0
                if msg.battery_remaining >= 0:
                    state["battery_pct"] = float(msg.battery_remaining)
            elif msg_type == "BATTERY_STATUS":
                if msg.battery_remaining >= 0:
                    state["battery_pct"] = float(msg.battery_remaining)
                volts = [v for v in msg.voltages if v not in (0, 65535)]
                if volts:
                    state["battery_voltage"] = sum(volts) / 1000.0
            elif msg_type == "EKF_STATUS_REPORT":
                flags = int(msg.flags)
                state["ekf_ok"] = flags in (7, 15, 31) or (flags & 0x1F) == 0x1F
                state["ekf_variance"] = float(msg.velocity_variance)
            elif msg_type == "DISTANCE_SENSOR":
                if msg.distance != 0:
                    state["rangefinder_m"] = msg.distance / 100.0
                    state["agl_m"] = state["rangefinder_m"]
            elif msg_type == "RADIO_STATUS":
                state["rssi"] = msg.rssi
                state["link_quality"] = max(0, min(100, int((msg.rssi / 255.0) * 100)))
            elif msg_type == "MISSION_CURRENT":
                state["mission_seq"] = msg.seq
            elif msg_type == "STATUSTEXT":
                text = msg.text.strip() if isinstance(msg.text, str) else str(msg.text)
                state["statustext"] = text
                severity = "warning" if msg.severity <= 4 else "info"
                self.bus.publish("fleet.statustext",
                                 {"callsign": callsign, "text": text, "severity_raw": msg.severity},
                                 severity=severity, source=callsign, data_quality="LIVE")
            elif msg_type == "COMMAND_ACK":
                self._on_command_ack(callsign, msg)
            elif msg_type == "TUNNEL":
                self._on_tunnel(callsign, msg)
            elif msg_type in {"MISSION_ACK", "MISSION_REQUEST", "MISSION_REQUEST_INT",
                              "MISSION_COUNT", "MISSION_ITEM_INT"}:
                self.bus.publish("mission.transfer", {"callsign": callsign, "message": msg_type},
                                 source=callsign, data_quality="LIVE", persist=False)
                if self.mission_message_handler:
                    self.mission_message_handler(callsign, msg)
            elif msg_type in {"PARAM_VALUE", "PARAM_ACK"}:
                if self.param_message_handler:
                    self.param_message_handler(callsign, msg)
        except Exception as exc:
            self.bus.publish("system.error", {"detail": f"MAVLink parse error: {exc}"},
                             severity="warning", source="TELEMETRY", data_quality="LIVE")

    def _on_heartbeat(self, sysid: int, callsign: str | None, msg: Any) -> None:
        if not callsign:
            now = time.monotonic()
            if now - self._last_unexpected.get(sysid, 0) > 10:
                self._last_unexpected[sysid] = now
                self.bus.publish(
                    "safety.interlock",
                    {"rule_id": "UNEXPECTED_SYSID", "sysid": sysid,
                     "detail": f"Heartbeat from unexpected system ID {sysid}; ignored"},
                    severity="warning", source="TELEMETRY", data_quality="LIVE",
                )
            return
        state = self.vehicles[callsign]
        now = time.monotonic()
        previous_valid = state["identity_valid"]
        is_armed = bool(msg.base_mode & HEARTBEAT_ARMED_FLAG)
        mode = ARDUPILOT_MODES.get(msg.custom_mode, f"MODE {msg.custom_mode}")
        autopilot_ok = msg.autopilot == MAV_AUTOPILOT_ARDUPILOTMEGA
        expected_type = MAV_TYPE_QUADROTOR if state["role"] == "SEARCH" else MAV_TYPE_HEXAROTOR
        type_ok = msg.type in (expected_type, 0)
        with self._lock:
            state.update(
                last_seen_mono=now,
                last_heartbeat=iso_now(),
                last_update=iso_now(),
                connected=True,
                stale=False,
                armed=is_armed,
                mode=mode,
                custom_mode=int(msg.custom_mode),
                source="MAVLINK",
                data_quality="LIVE",
                identity_valid=autopilot_ok and type_ok,
                identity_note=(
                    "ArduPilot identity verified" if autopilot_ok and type_ok
                    else f"Identity mismatch (autopilot={msg.autopilot}, type={msg.type})"
                ),
            )
        if not previous_valid and state["identity_valid"]:
            self.bus.publish("fleet.connection",
                             {"callsign": callsign, "sysid": sysid, "identity_valid": True},
                             source="TELEMETRY", data_quality="LIVE")
        elif previous_valid and not state["identity_valid"]:
            self.bus.publish("safety.interlock",
                             {"rule_id": "IDENTITY_MISMATCH", "callsign": callsign, "detail": state["identity_note"]},
                             severity="warning", source="TELEMETRY", data_quality="LIVE")

    def _on_tunnel(self, callsign: str, msg: Any) -> None:
        try:
            payload_raw = bytes(msg.payload)
            envelope = json.loads(payload_raw.decode("utf-8").rstrip("\x00"))
            event_type = envelope.get("type") or envelope.get("event_type")
            if self.project_event_handler and event_type:
                self.project_event_handler(callsign, envelope)
        except Exception as exc:
            self.bus.publish("safety.interlock",
                             {"rule_id": "EVENT_DECODE", "callsign": callsign, "detail": f"Undecodable project event: {exc}"},
                             severity="warning", source=callsign, data_quality="LIVE")

    # ----------------------------------------------------------------- commands
    def _settle(self, tx: CommandTransaction, kind: str, *, severity: str = "info") -> None:
        quality = "SIMULATED" if tx.simulated else "LIVE"
        self.bus.publish(kind, {"tx": tx.to_dict()}, severity=severity,
                         source=tx.callsign if self.mode == "REAL" else "GCS",
                         data_quality=quality)
        if tx.state in SETTLING_TX_STATES and self.command_settled_handler:
            try:
                self.command_settled_handler(tx.tx_id)
            except Exception as exc:
                self.bus.publish(
                    "system.error",
                    {"detail": f"Command settle handler failed for {tx.tx_id}: {exc}"},
                    severity="critical", source="GCS", data_quality=quality,
                )

    def _expectation_for(self, command: int, params: list[float]) -> dict[str, Any] | None:
        """What the vehicle state must show before an accepted command can be called done."""
        def p(index: int) -> float:
            return float(params[index]) if len(params) > index else 0.0

        if command == MAV_CMD_DO_SET_MODE:
            return {"kind": "mode", "value": int(p(1)), "describe": f"custom mode {int(p(1))}"}
        if command == MAV_CMD_COMPONENT_ARM_DISARM:
            want = bool(p(0))
            return {"kind": "armed" if want else "disarmed",
                    "describe": "armed heartbeat" if want else "disarmed heartbeat"}
        if command == MAV_CMD_NAV_TAKEOFF:
            return {"kind": "altitude_above_m", "value": max(1.0, p(6) * 0.8),
                    "describe": f"relative altitude above {max(1.0, p(6) * 0.8):.1f} m"}
        if command == MAV_CMD_NAV_LAND:
            return {"kind": "altitude_below_m", "value": 1.0, "describe": "relative altitude below 1 m"}
        if command == MAV_CMD_NAV_RETURN_TO_LAUNCH:
            return {"kind": "mode_in", "value": [6, 21, 7], "describe": "RTL / SMART_RTL / CIRCLE mode"}
        if command == MAV_CMD_DO_REPOSITION:
            return {"kind": "mode_in", "value": [4, 16, 5, 17], "describe": "GUIDED / POSHOLD / LOITER / BRAKE mode"}
        return None

    def _expectation_met(self, tx: CommandTransaction) -> bool:
        exp = tx.expectation
        if not exp:
            return False
        state = self.vehicles.get(tx.callsign)
        if not state:
            return False
        kind = exp.get("kind")
        custom_mode = state.get("custom_mode")
        if kind == "mode":
            return custom_mode is not None and int(custom_mode) == int(exp["value"])
        if kind == "mode_in":
            return custom_mode is not None and int(custom_mode) in {int(v) for v in exp["value"]}
        if kind == "armed":
            return bool(state.get("armed"))
        if kind == "disarmed":
            return custom_mode is not None and not state.get("armed")
        alt = state.get("alt_rel_m")
        if kind == "altitude_above_m":
            return alt is not None and float(alt) >= float(exp["value"])
        if kind == "altitude_below_m":
            return alt is not None and float(alt) <= float(exp["value"])
        return False

    def _mark_verified(self, tx: CommandTransaction) -> None:
        tx.state = "VERIFIED"
        tx.verified_at = iso_now()
        tx.updated_at = iso_now()
        tx.timeout_at = None
        tx.verify_deadline = None
        tx.detail = "Vehicle state confirms the command"
        self._settle(tx, "command.verified")

    def simulate_ack(self, tx_id: str) -> None:
        tx = self._transactions.get(tx_id)
        if not tx:
            return
        tx.state = "ACKNOWLEDGED"
        tx.ack_result = 0
        tx.ack_at = iso_now()
        tx.updated_at = iso_now()
        tx.timeout_at = None
        tx.verify_deadline = None
        tx.detail = "ACCEPTED (simulated vehicle; simulated acceptance is not flight evidence)"
        self._settle(tx, "command.ack")

    def _address_block(self, callsign: str, state: dict[str, Any],
                       transport: MavlinkTransport | None) -> str:
        """REAL-mode gate: never address a vehicle the GCS cannot currently see."""
        if not transport:
            return "No MAVLink transport is open for this vehicle"
        if state.get("source") != "MAVLINK":
            return f"No MAVLink traffic received from {callsign} on this link (source={state.get('source')})"
        if not state.get("identity_valid"):
            return f"Vehicle identity is not verified: {state.get('identity_note') or 'no heartbeat'}"
        if not state.get("connected"):
            return "Vehicle link is down"
        if state.get("stale"):
            return f"Telemetry is stale (last heartbeat {state.get('heartbeat_age_s')} s ago)"
        return ""

    def send_command(
        self,
        callsign: str,
        command: int,
        params: list[float],
        label: str,
        *,
        confirmation: int = 0,
    ) -> CommandTransaction:
        state = self.vehicles[callsign]
        tx = CommandTransaction(
            tx_id=new_id("TX"),
            callsign=callsign,
            command=int(command),
            label=label,
            params=params,
            simulated=self.mode == "SIMULATION",
        )
        if self.mode == "REAL":
            tx.expectation = self._expectation_for(int(command), list(params))
        self._transactions[tx.tx_id] = tx
        tx.state = "SENT"
        tx.attempts = 1
        timeout_s = float(self.config.get("telemetry", "command_timeout_s", default=2.5))
        tx.timeout_at = time.monotonic() + timeout_s
        self.bus.publish("command.sent", {"tx": tx.to_dict(), "params": params},
                         source="GCS", data_quality=state.get("data_quality", "UNKNOWN"))
        if self.mode == "SIMULATION":
            handled = True
            if self.sim_command_handler:
                try:
                    handled = bool(self.sim_command_handler(callsign, command, params))
                except Exception:
                    handled = False
            if handled:
                self.simulate_ack(tx.tx_id)
            else:
                tx.state = "REJECTED"
                tx.detail = "Simulation rejected command"
                tx.timeout_at = None
                self._settle(tx, "command.rejected", severity="warning")
        else:
            transport = self.transports.get(callsign) or self.shared_transport
            block_reason = self._address_block(callsign, state, transport)
            if block_reason:
                tx.state = "REJECTED"
                tx.detail = f"NOT_ADDRESSED — {block_reason}"
                tx.timeout_at = None
                self._settle(tx, "command.rejected", severity="critical")
                self.bus.publish(
                    "safety.interlock",
                    {"rule_id": "COMMAND_NOT_ADDRESSED", "callsign": callsign,
                     "command": int(command), "detail": block_reason},
                    severity="critical", source="TELEMETRY", data_quality="LIVE",
                )
            else:
                try:
                    transport.command_long(
                        int(state["sysid"]), 1, command, params, confirmation=confirmation
                    )
                    # Only after the write returned: this is the line the payload ledger uses
                    # to decide whether a kit's fate is unknown or still aboard.
                    tx.transmitted = True
                    if tx.expectation:
                        tx.verify_deadline = (
                            time.monotonic() + timeout_s
                            + float(self.config.get("telemetry", "state_verify_s", default=8.0))
                        )
                except Exception as exc:
                    tx.state = "REJECTED"
                    tx.detail = str(exc)
                    tx.timeout_at = None
                    self._settle(tx, "command.rejected", severity="warning")
        if len(self._transactions) > 500:
            for stale_id, stale_tx in list(self._transactions.items())[:-200]:
                if stale_tx.state in TERMINAL_TX_STATES:
                    del self._transactions[stale_id]
        return tx

    def _sweep_transactions(self, now: float) -> None:
        """Time out unanswered commands and resolve state verification.

        Driven from the monitor loop rather than ad-hoc asyncio tasks: commands can be
        issued from the MAVLink reader thread, where there is no running loop, and bare
        tasks without a strong reference can be garbage-collected before they fire.
        """
        for tx in list(self._transactions.values()):
            if tx.state in TERMINAL_TX_STATES:
                continue
            if tx.state in {"SENT", "RETRYING"}:
                if tx.timeout_at and now >= tx.timeout_at:
                    tx.state = "TIMEOUT"
                    tx.detail = f"No COMMAND_ACK within {self.config.get('telemetry', 'command_timeout_s', default=2.5)} s"
                    tx.timeout_at = None
                    tx.verify_deadline = None
                    self._settle(tx, "command.timeout", severity="warning")
                continue
            if tx.state == "ACKNOWLEDGED":
                if not tx.expectation:
                    continue
                if self._expectation_met(tx):
                    self._mark_verified(tx)
                elif tx.verify_deadline and now >= tx.verify_deadline:
                    tx.state = "ACK_UNVERIFIED"
                    tx.detail = (
                        f"Autopilot accepted the command but the vehicle never showed "
                        f"{tx.expectation.get('describe')}; the physical action is NOT confirmed"
                    )
                    tx.verify_deadline = None
                    self._settle(tx, "command.unverified", severity="critical")

    def _on_command_ack(self, callsign: str, msg: Any) -> None:
        src_component = int(getattr(msg, "get_srcComponent", lambda: 1)())
        if src_component != 1:
            # Only the autopilot component can accept a flight command. A camera, gimbal or
            # companion computer ACK is not evidence that the vehicle took the command.
            return
        for tx in reversed(list(self._transactions.values())):
            if tx.callsign == callsign and tx.command == int(msg.command) and tx.state in {"SENT", "RETRYING"}:
                tx.ack_result = int(msg.result)
                tx.updated_at = iso_now()
                tx.ack_at = iso_now()
                tx.timeout_at = None
                if int(msg.result) == 0:
                    tx.state = "ACKNOWLEDGED"
                    tx.detail = (
                        "ACCEPTED — awaiting vehicle state verification"
                        if tx.expectation else "ACCEPTED (this command has no vehicle-state check)"
                    )
                    self._settle(tx, "command.ack")
                    if tx.expectation and self._expectation_met(tx):
                        self._mark_verified(tx)
                else:
                    tx.state = "REJECTED"
                    tx.detail = f"MAV_RESULT {msg.result}"
                    tx.verify_deadline = None
                    self._settle(tx, "command.ack", severity="warning")
                return

    def transaction(self, tx_id: str) -> dict[str, Any] | None:
        tx = self._transactions.get(tx_id)
        return tx.to_dict() if tx else None

    def transactions(self) -> list[dict[str, Any]]:
        return [tx.to_dict() for tx in list(self._transactions.values())[-100:]]

    # ------------------------------------------------------------------ loops
    async def start_monitors(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())
        if self._persist_task is None or self._persist_task.done():
            self._persist_task = asyncio.create_task(self._persist_loop())

    async def _monitor_loop(self) -> None:
        timeout_s = float(self.config.get("telemetry", "heartbeat_timeout_s", default=3.0))
        stale_s = float(self.config.get("telemetry", "stale_after_s", default=2.0))
        while True:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            changed = []
            self._sweep_transactions(now)
            for callsign, state in self.vehicles.items():
                if self.mode != "SIMULATION" and state["source"] != "MAVLINK":
                    # REAL mode: a record that never carried MAVLink traffic (left over from
                    # a simulation, or a vehicle that has simply not answered yet) is LOST,
                    # not "quietly skipped" while still reporting connected=True.
                    if state["connected"] or not state["stale"]:
                        state["connected"] = False
                        state["stale"] = True
                        state["link"] = "LOST"
                        state["data_quality"] = "LOST"
                        state["identity_valid"] = False
                        state["identity_note"] = "No MAVLink source on this link in REAL mode"
                        changed.append(callsign)
                    continue
                age = now - float(state.get("last_seen_mono") or 0)
                state["heartbeat_age_s"] = round(age, 2)
                was_connected = state["connected"]
                if age > timeout_s:
                    state["connected"] = False
                    state["stale"] = True
                    state["link"] = "LOST"
                    state["data_quality"] = "LOST"
                    state["identity_valid"] = False
                elif age > stale_s:
                    state["stale"] = True
                    state["link"] = "STALE"
                    state["data_quality"] = "STALE"
                else:
                    state["stale"] = False
                    state["link"] = "OK" if state["source"] != "SIMULATED" else "SIM"
                    state["data_quality"] = "SIMULATED" if state["source"] == "SIMULATED" else "LIVE"
                if was_connected != state["connected"]:
                    changed.append(callsign)
                    self.bus.publish(
                        "fleet.connection",
                        {"callsign": callsign, "connected": state["connected"], "age_s": round(age, 2)},
                        severity="warning" if not state["connected"] else "info",
                        source="TELEMETRY", data_quality=state["data_quality"],
                    )
            if self.mode == "REAL":
                self.bus.publish("fleet.telemetry", self.snapshot(), source="TELEMETRY",
                                 data_quality="LIVE", persist=False)

    async def _persist_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            mission_id = self.bus.mission_id
            if not mission_id:
                continue
            now = time.monotonic()
            for callsign, state in self.vehicles.items():
                if state["data_quality"] not in {"LIVE", "SIMULATED"}:
                    continue
                if now - self._last_persist.get(callsign, 0) < 0.5:
                    continue
                self._last_persist[callsign] = now
                try:
                    self.db.record_telemetry(mission_id, state)
                except Exception:
                    pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode,
                "connected": self.connected,
                "topology": self.topology,
                "topology_status": self.topology_status,
                # The physical commissioning record, not the configured intent: without it
                # "topology: shared" could read on screen as though the link were proven.
                "topology_commissioned": bool(
                    self.config.get("telemetry", "topology_verified", default=False)
                ),
                "vehicles": [dict(v) for v in self.vehicles.values()],
                "transactions": self.transactions(),
            }

    def vehicle(self, callsign: str) -> dict[str, Any]:
        return self.vehicles[callsign]

    def all_fresh(self) -> bool:
        for v in self.vehicles.values():
            if not (v["connected"] and not v["stale"] and v["identity_valid"]):
                return False
            # In REAL mode only live MAVLink may satisfy the gate; simulated or stale rows
            # must never let a launch proceed on cached data.
            if self.mode == "REAL" and (v["source"] != "MAVLINK" or v["data_quality"] != "LIVE"):
                return False
        return True

    def freshness_report(self) -> dict[str, str]:
        """Per-vehicle reason for failing the gate, for the preflight panel."""
        report: dict[str, str] = {}
        for callsign, v in self.vehicles.items():
            reasons = []
            if not v["connected"]:
                reasons.append("link down")
            if v["stale"]:
                reasons.append(f"telemetry stale ({v.get('heartbeat_age_s')} s)")
            if not v["identity_valid"]:
                reasons.append(v.get("identity_note") or "identity unverified")
            if self.mode == "REAL" and v["source"] != "MAVLINK":
                reasons.append(f"no MAVLink source (cached as {v['source']})")
            if reasons:
                report[callsign] = "; ".join(reasons)
        return report
