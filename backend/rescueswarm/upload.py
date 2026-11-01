"""Mission, fence and parameter upload with read-back verification.

SIMULATION produces labelled simulated receipts from the plan itself. REAL mode
uses the MAVLink mission protocol (MISSION_COUNT → MISSION_REQUEST_INT →
MISSION_ITEM_INT → MISSION_ACK) followed by a MISSION_REQUEST_LIST read-back
comparison. Nothing is reported VERIFIED unless the read-back matches.

REAL upload has not been executed against hardware in this repository. It is a
PENDING TEST implementation and readiness refuses to rely on simulated receipts
when the GCS is in REAL mode.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .config import Config
from .telemetry import TelemetryManager
from .util import iso_now

MAV_MISSION_TYPE_MISSION = 0
MAV_MISSION_TYPE_FENCE = 1

FENCE_POLYGON_INCLUSION = 5001
FENCE_RETURN_POINT = 5002
MAV_FRAME_GLOBAL_INT = 5

# ArduPilot answers MISSION_REQUEST_INT with the INT alias of the frame that was
# sent (GLOBAL ↔ GLOBAL_INT, GLOBAL_RELATIVE_ALT ↔ GLOBAL_RELATIVE_ALT_INT,
# TERRAIN_ALT ↔ TERRAIN_ALT_INT), so only a different frame *class* is a real
# mismatch. Values verified against the installed pymavlink 2.4.50 common dialect.
FRAME_CLASSES = {
    0: "global", 5: "global",
    3: "global_relative_alt", 6: "global_relative_alt",
    10: "global_terrain_alt", 11: "global_terrain_alt",
    2: "mission",
    1: "local", 4: "local", 7: "local_offset", 20: "local", 21: "local",
    8: "body", 9: "body_offset", 12: "body",
}

# A coordinate that differs by more than ~0.11 m is a different waypoint. MISSION_ITEM_INT
# carries integer 1e-7 degrees, so this is the smallest tolerance that is not pure float noise.
COORD_TOL_DEG = 1e-6
ALT_TOL_M = 0.2
PARAM_TOL = 1e-3


def _frame_class(frame: Any) -> str:
    """Normalise a MAV_FRAME id to its class, or 'unknown' when it is not a frame."""
    try:
        return FRAME_CLASSES.get(int(frame), "unknown")
    except (TypeError, ValueError):
        return "unknown"


class UploadError(RuntimeError):
    pass


class _Session:
    def __init__(self, kind: str, items: list[dict[str, Any]] | None = None) -> None:
        self.kind = kind
        self.items = items or []
        self.expected = len(self.items) if items else None
        self.received: dict[int, dict[str, Any]] = {}
        self.ack: int | None = None
        self.params: dict[str, float] = {}
        self.expected_names: set[str] = set()
        self.received_names: set[str] = set()
        self.requested: set[int] = set()
        self.done = threading.Event()
        self.error: str | None = None


class MissionUploader:
    def __init__(self, config: Config, telemetry: TelemetryManager) -> None:
        self.config = config
        self.telemetry = telemetry
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        telemetry.mission_message_handler = self.handle_mission_message
        telemetry.param_message_handler = self.handle_param_message

    # ---------------------------------------------------------------- public
    def upload_plan(self, plan: dict[str, Any], mode: str) -> dict[str, Any]:
        if mode == "SIMULATION":
            return self._simulated_receipts(plan)
        receipts: dict[str, Any] = {}
        for profile in self.config.fleet:
            callsign = profile["callsign"]
            receipts[callsign] = self._upload_aircraft(profile, plan)
        return {
            "aircraft": receipts,
            "uploaded_at": iso_now(),
            "mode": "REAL",
            "plan_hash": plan.get("plan_hash"),
            "rule": "A receipt is VERIFIED only when upload and read-back both match.",
        }

    # --------------------------------------------------------------- simulate
    def _simulated_receipts(self, plan: dict[str, Any]) -> dict[str, Any]:
        receipts: dict[str, Any] = {}
        for profile in self.config.fleet:
            callsign = profile["callsign"]
            mission_count = len(plan.get("mission_items", [])) if profile["role"] == "SEARCH" else 0
            fence = plan.get("geofences", {}).get(callsign, {})
            fence_count = len(fence.get("polygon", [])) + 1
            receipts[callsign] = {
                "callsign": callsign,
                "status": "VERIFIED",
                "detail": "Simulated upload and read-back match; no hardware was addressed",
                "plan_hash": plan.get("plan_hash"),
                "mission_count": mission_count,
                "fence_count": fence_count,
                "parameters": self._parameter_plan(callsign, plan),
                "simulated": True,
                "evidence": "SIMULATED",
                "verified_at": iso_now(),
                "streams": {
                    "mission": {"status": "VERIFIED", "count": mission_count, "simulated": True},
                    "fence": {"status": "VERIFIED", "count": fence_count, "simulated": True},
                    "parameters": {"status": "VERIFIED", "simulated": True},
                },
            }
        return {
            "aircraft": receipts,
            "uploaded_at": iso_now(),
            "mode": "SIMULATION",
            "plan_hash": plan.get("plan_hash"),
            "rule": "Simulated receipts carry no flight evidence.",
        }

    # ------------------------------------------------------------------- real
    def _upload_aircraft(self, profile: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        callsign = profile["callsign"]
        streams: dict[str, Any] = {}
        mission_items = (
            [self._mission_item(item) for item in plan.get("mission_items", [])]
            if profile["role"] == "SEARCH"
            else []
        )
        if mission_items:
            streams["mission"] = self._upload_and_verify(callsign, profile["sysid"], mission_items,
                                                         MAV_MISSION_TYPE_MISSION)
        else:
            streams["mission"] = {"status": "NOT_REQUIRED", "count": 0}

        fence_items = self._fence_items(plan.get("geofences", {}).get(callsign, {}))
        if fence_items:
            streams["fence"] = self._upload_and_verify(callsign, profile["sysid"], fence_items,
                                                       MAV_MISSION_TYPE_FENCE)
        else:
            streams["fence"] = {"status": "FAIL", "detail": "No inclusion fence in plan"}

        params = self._parameter_plan(callsign, plan)
        streams["parameters"] = self._set_and_verify_params(callsign, profile["sysid"], params)

        status = self._overall_status(streams, mission_required=bool(mission_items))
        wire = {}
        for stream_name, stream in streams.items():
            entry = {
                "status": stream.get("status"),
                "count": stream.get("count"),
                "detail": stream.get("detail"),
                "simulated": False,
            }
            if stream.get("mismatches"):
                entry["mismatches"] = stream["mismatches"][:12]
            wire[stream_name] = entry
        return {
            "callsign": callsign,
            "status": status,
            "detail": self._receipt_detail(streams),
            "plan_hash": plan.get("plan_hash"),
            "mission_count": len(mission_items),
            "fence_count": len(fence_items),
            "parameters": params,
            "simulated": False,
            "evidence": "PENDING TEST",
            "verified_at": iso_now(),
            "streams": wire,
        }

    @staticmethod
    def _overall_status(streams: dict[str, Any], mission_required: bool) -> str:
        """Aggregate per-stream results by what the aircraft actually needs to fly safely.

        The fence and, for a search aircraft, the mission are flight-critical: a receipt
        must not read PARTIAL when the thing that failed is the one that protects the
        vehicle. Parameters sit below them because a written-but-unreadable parameter is
        still a parameter the vehicle may already hold.
        """
        critical = ["fence"] + (["mission"] if mission_required else [])
        for name in critical:
            stream_status = (streams.get(name) or {}).get("status")
            if stream_status == "FAIL":
                return "FAILED"
            if stream_status != "VERIFIED":
                return "UNVERIFIED"
        if (streams.get("parameters") or {}).get("status") not in {"VERIFIED", "NOT_REQUIRED"}:
            return "PARTIAL"
        return "VERIFIED"

    @staticmethod
    def _receipt_detail(streams: dict[str, Any]) -> str:
        summary = "; ".join(f"{name}: {stream.get('status')}" for name, stream in streams.items())
        first_problem = next(
            (stream.get("detail") for stream in streams.values()
             if stream.get("status") not in {"VERIFIED", "NOT_REQUIRED"} and stream.get("detail")),
            None,
        )
        return f"{summary}. {first_problem}" if first_problem else summary

    def _transport(self, callsign: str) -> Any:
        transport = self.telemetry.transports.get(callsign) or self.telemetry.shared_transport
        if not transport:
            raise UploadError(f"No telemetry transport for {callsign}")
        return transport

    def _upload_and_verify(
        self,
        callsign: str,
        sysid: int,
        items: list[dict[str, Any]],
        mission_type: int,
    ) -> dict[str, Any]:
        try:
            transport = self._transport(callsign)
            upload = self._upload(callsign, transport, sysid, items, mission_type)
            if upload["status"] != "VERIFIED":
                return upload
            readback = self._readback(callsign, transport, sysid, mission_type, items)
            if readback["status"] != "VERIFIED":
                readback["detail"] = f"Upload acknowledged but {readback.get('detail', 'read-back failed')}"
                return readback
            return {
                "status": "VERIFIED",
                "count": len(items),
                "detail": "Upload and read-back match item by item",
            }
        except Exception as exc:
            return {"status": "FAIL", "count": len(items), "detail": str(exc)}

    def _upload(
        self,
        callsign: str,
        transport: Any,
        sysid: int,
        items: list[dict[str, Any]],
        mission_type: int,
    ) -> dict[str, Any]:
        session = _Session("upload", items)
        with self._lock:
            self._sessions[callsign] = session
        try:
            transport.mission_count(sysid, 1, len(items), mission_type)
            if not session.done.wait(self._timeout("upload_timeout_s", 20.0)):
                return {"status": "UNVERIFIED", "detail": "No MISSION_ACK before timeout"}
            if session.error:
                return {"status": "FAIL", "detail": session.error}
            if session.ack != 0:
                return {"status": "FAIL", "detail": f"MISSION_ACK result {session.ack}"}
            return {"status": "VERIFIED", "count": len(items), "detail": "MISSION_ACK accepted"}
        finally:
            with self._lock:
                if self._sessions.get(callsign) is session:
                    self._sessions.pop(callsign, None)

    def _readback(
        self,
        callsign: str,
        transport: Any,
        sysid: int,
        mission_type: int,
        expected_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        session = _Session("readback")
        with self._lock:
            self._sessions[callsign] = session
        try:
            transport.mission_request_list(sysid, 1, mission_type)
            if not session.done.wait(self._timeout("readback_timeout_s", 20.0)):
                return {"status": "UNVERIFIED", "detail": "Read-back timed out"}
            if session.error:
                return {"status": "FAIL", "detail": session.error}
            if session.expected is None:
                return {"status": "FAIL", "detail": "Read-back returned no MISSION_COUNT"}
            if len(session.received) != session.expected:
                return {
                    "status": "FAIL",
                    "detail": f"Read-back returned {len(session.received)}/{session.expected} items",
                }
            # A matching count proves nothing about content: a shifted or stale
            # mission of the same length would otherwise be reported VERIFIED.
            mismatches = self._compare_items(expected_items, session.received)
            if mismatches:
                return {
                    "status": "FAIL",
                    "count": session.expected,
                    "detail": "Read-back differs from the uploaded items: "
                              + "; ".join(mismatches[:6])
                              + (f" (+{len(mismatches) - 6} more)" if len(mismatches) > 6 else ""),
                    "mismatches": mismatches,
                }
            return {"status": "VERIFIED", "count": session.expected, "items": session.received}
        finally:
            with self._lock:
                if self._sessions.get(callsign) is session:
                    self._sessions.pop(callsign, None)

    @staticmethod
    def _compare_items(expected: list[dict[str, Any]],
                       received: dict[int, dict[str, Any]]) -> list[str]:
        """Compare every read-back waypoint against what was actually sent."""
        problems: list[str] = []
        for seq, sent in enumerate(expected):
            got = received.get(seq)
            if not got:
                problems.append(f"#{seq} missing from read-back")
                continue
            if int(got.get("command", -1)) != int(sent["command"]):
                problems.append(f"#{seq} command {got.get('command')} != {sent['command']}")
            if _frame_class(got.get("frame")) != _frame_class(sent["frame"]):
                problems.append(f"#{seq} frame {got.get('frame')} != {sent['frame']}")
            for key, wire_key in (("lat", "lat"), ("lon", "lon")):
                want, have = float(sent[key]), got.get(wire_key)
                if have is None or abs(want - float(have)) > COORD_TOL_DEG:
                    problems.append(f"#{seq} {key} {have} != {want}")
            want_alt, have_alt = float(sent["alt"]), got.get("alt")
            if have_alt is None or abs(want_alt - float(have_alt)) > ALT_TOL_M:
                problems.append(f"#{seq} altitude {have_alt} != {want_alt}")
            for index in range(1, 5):
                param = f"p{index}"
                want_p, have_p = float(sent.get(param, 0.0)), float(got.get(param, 0.0))
                if abs(want_p - have_p) > PARAM_TOL:
                    problems.append(f"#{seq} {param} {have_p} != {want_p}")
        extra = set(received) - set(range(len(expected)))
        if extra:
            problems.append(f"read-back contains unexpected sequences {sorted(extra)}")
        return problems

    def _set_and_verify_params(
        self, callsign: str, sysid: int, params: dict[str, float]
    ) -> dict[str, Any]:
        if not params:
            return {"status": "NOT_REQUIRED"}
        try:
            transport = self._transport(callsign)
            write_session = _Session("params_write")
            write_session.expected_names = set(params)
            with self._lock:
                self._sessions[callsign] = write_session
            try:
                for name, value in params.items():
                    transport.param_set(sysid, 1, name, value)
                if not write_session.done.wait(self._timeout("param_timeout_s", 8.0)):
                    return {"status": "UNVERIFIED", "detail": "Parameter write not acknowledged"}
                if write_session.error:
                    return {"status": "FAIL", "detail": write_session.error}
            finally:
                with self._lock:
                    if self._sessions.get(callsign) is write_session:
                        self._sessions.pop(callsign, None)

            read_session = _Session("params_read")
            read_session.expected_names = set(params)
            with self._lock:
                self._sessions[callsign] = read_session
            try:
                for name in params:
                    transport.param_request_read(sysid, 1, name)
                if not read_session.done.wait(self._timeout("param_timeout_s", 8.0)):
                    return {"status": "UNVERIFIED", "detail": "Parameter read-back timed out"}
                if read_session.error:
                    return {"status": "FAIL", "detail": read_session.error}
                mismatches = []
                for name, expected in params.items():
                    actual = read_session.params.get(name)
                    if actual is None or abs(float(actual) - float(expected)) > max(
                        1e-3, abs(float(expected)) * 1e-3
                    ):
                        mismatches.append(f"{name}={actual} (expected {expected})")
                if mismatches:
                    return {"status": "FAIL", "detail": "; ".join(mismatches)}
                return {"status": "VERIFIED", "count": len(params)}
            finally:
                with self._lock:
                    if self._sessions.get(callsign) is read_session:
                        self._sessions.pop(callsign, None)
        except Exception as exc:
            return {"status": "FAIL", "detail": str(exc)}

    # --------------------------------------------------------------- handlers
    def handle_mission_message(self, callsign: str, msg: Any) -> None:
        with self._lock:
            session = self._sessions.get(callsign)
        if not session:
            return
        message_type = msg.get_type()
        try:
            if message_type in {"MISSION_REQUEST_INT", "MISSION_REQUEST"}:
                seq = int(msg.seq)
                mission_type = int(getattr(msg, "mission_type", MAV_MISSION_TYPE_MISSION))
                if session.kind == "upload":
                    if not 0 <= seq < len(session.items):
                        session.error = f"Vehicle requested out-of-range item {seq}"
                        session.done.set()
                        return
                    item = session.items[seq]
                    transport = self._transport(callsign)
                    transport.mission_item_int(
                        int(msg.get_srcSystem()), 1, seq, item["frame"], item["command"],
                        1 if seq == 0 else 0, 1,
                        item["p1"], item["p2"], item["p3"], item["p4"],
                        item["lat"], item["lon"], item["alt"],
                        mission_type,
                    )
            elif message_type == "MISSION_COUNT":
                if session.kind == "readback":
                    session.expected = int(msg.count)
                    transport = self._transport(callsign)
                    mission_type = int(getattr(msg, "mission_type", MAV_MISSION_TYPE_MISSION))
                    for seq in range(session.expected):
                        transport.mission_request_int(int(msg.get_srcSystem()), 1, seq, mission_type)
                    if session.expected == 0:
                        session.done.set()
            elif message_type == "MISSION_ITEM_INT":
                if session.kind == "readback":
                    seq = int(msg.seq)
                    session.received[seq] = {
                        "seq": seq,
                        "command": int(msg.command),
                        "frame": int(msg.frame),
                        "lat": msg.x / 1e7,
                        "lon": msg.y / 1e7,
                        "alt": float(msg.z),
                        "p1": float(msg.param1),
                        "p2": float(msg.param2),
                        "p3": float(msg.param3),
                        "p4": float(msg.param4),
                    }
                    if session.expected is not None and len(session.received) >= session.expected:
                        session.done.set()
            elif message_type == "MISSION_ACK":
                if session.kind == "upload":
                    session.ack = int(msg.type)
                    session.done.set()
        except Exception as exc:
            session.error = str(exc)
            session.done.set()

    def handle_param_message(self, callsign: str, msg: Any) -> None:
        with self._lock:
            session = self._sessions.get(callsign)
        if not session or not session.kind.startswith("params"):
            return
        try:
            if msg.get_type() in {"PARAM_VALUE", "PARAM_ACK"}:
                name = msg.param_id
                if isinstance(name, bytes):
                    name = name.decode("ascii", errors="ignore")
                name = name.rstrip("\x00")
                if name in session.expected_names:
                    session.params[name] = float(msg.param_value)
                    session.received_names.add(name)
                    if session.expected_names.issubset(session.received_names):
                        session.done.set()
        except Exception as exc:
            session.error = str(exc)
            session.done.set()

    # ------------------------------------------------------------------ helpers
    def _timeout(self, key: str, default: float) -> float:
        return float(self.config.get("telemetry", key, default=default))

    @staticmethod
    def _mission_item(item: dict[str, Any]) -> dict[str, Any]:
        """Map a plan waypoint to the wire fields, carrying any authored parameters.

        The shipped planner emits TAKEOFF / WAYPOINT / RTL with no params, but a
        hand-authored or imported mission may set them. Dropping them silently would
        upload a different mission than the one the operator approved.
        """
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        def param(index: int) -> float:
            for source in (f"p{index}", f"param{index}"):
                if item.get(source) is not None:
                    return float(item[source])
            return float(params.get(source, 0.0))
        return {
            "frame": int(item.get("frame", 6)),
            "command": int(item["command"]),
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "alt": float(item.get("alt_m", 0.0)),
            "p1": param(1), "p2": param(2), "p3": param(3), "p4": param(4),
        }

    def _fence_items(self, geofence: dict[str, Any]) -> list[dict[str, Any]]:
        polygon = geofence.get("polygon", [])
        if len(polygon) < 4:
            return []
        # A closed ring repeats the first vertex; ArduPilot expects unique vertices.
        vertices = polygon[:-1] if polygon[0] == polygon[-1] else polygon
        items = []
        for point in vertices:
            lon, lat = float(point[0]), float(point[1])
            items.append({
                "frame": MAV_FRAME_GLOBAL_INT,
                "command": FENCE_POLYGON_INCLUSION,
                "lat": lat, "lon": lon, "alt": 0.0,
                "p1": float(len(vertices)), "p2": 0.0, "p3": 0.0, "p4": 0.0,
            })
        first_lon, first_lat = float(vertices[0][0]), float(vertices[0][1])
        items.append({
            "frame": MAV_FRAME_GLOBAL_INT,
            "command": FENCE_RETURN_POINT,
            "lat": first_lat, "lon": first_lon, "alt": 0.0,
            "p1": 0.0, "p2": 0.0, "p3": 0.0, "p4": 0.0,
        })
        return items

    def _parameter_plan(self, callsign: str, plan: dict[str, Any]) -> dict[str, float]:
        profile = self.config.vehicle(callsign)
        fence = plan.get("geofences", {}).get(callsign, {})
        # FENCE_TYPE is a bitmask: 1 = altitude, 2 = circle/map, 4 = inclusion polygon.
        # The plan authors a polygon and an altitude ceiling but no circle radius, so
        # enabling bit 1 would arm a breach monitor whose trigger distance is whatever
        # FENCE_RADIUS happens to hold on the vehicle. Circle protection is therefore
        # deliberately left off; enabling it needs a plan field and a hardware test.
        params = {
            "FENCE_ENABLE": 1.0,
            "FENCE_TYPE": 5.0,  # altitude + inclusion polygon
            "FENCE_ACTION": 1.0,  # RTL
            "FENCE_ALT_MAX": float(fence.get("max_alt_m", 50.0)),
            "RTL_ALT": float(profile.get("rtl_alt_m", 25.0)) * 100.0,
            "FS_THR_ENABLE": 1.0,
            "BATT_FS_LOW_ACT": 2.0,
            "BATT_FS_CRT_ACT": 1.0,
        }
        if profile["role"] == "SEARCH":
            params["WPNAV_SPEED"] = float(profile.get("search_speed_ms", 8.0)) * 100.0
        else:
            params["WPNAV_SPEED"] = float(profile.get("delivery_speed_ms", 10.0)) * 100.0
        return params
