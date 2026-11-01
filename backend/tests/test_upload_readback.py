"""REAL-mode upload receipts: a matching item count is not a matching mission.

These tests drive the mission protocol against an in-process stand-in for the
vehicle. The stand-in is honest by default and deliberately dishonest on request,
so the receipt logic is exercised for the case that matters: a flight controller
that acknowledges an upload and then reports something different.

Nothing here touches hardware. The REAL path stays labelled PENDING TEST.
"""

from __future__ import annotations

from typing import Any

import pytest

from rescueswarm.upload import (
    FENCE_POLYGON_INCLUSION,
    FENCE_RETURN_POINT,
    MAV_MISSION_TYPE_FENCE,
    MAV_MISSION_TYPE_MISSION,
    MissionUploader,
)

CALLSIGNS = ("INS-01", "DEL-01", "DEL-02")

# Closed ring: first vertex repeated, as the planner emits it.
POLYGON = [
    [77.6000, 12.9400],
    [77.6040, 12.9400],
    [77.6040, 12.9440],
    [77.6000, 12.9440],
    [77.6000, 12.9400],
]

MISSION_ITEMS = [
    {"seq": 0, "command": 22, "frame": 6, "lat": 12.9405, "lon": 77.6015, "alt_m": 40.0},
    {"seq": 1, "command": 16, "frame": 6, "lat": 12.9415, "lon": 77.6025, "alt_m": 40.0},
    {"seq": 2, "command": 16, "frame": 6, "lat": 12.9425, "lon": 77.6035, "alt_m": 40.0},
    {"seq": 3, "command": 20, "frame": 6, "lat": 12.9405, "lon": 77.6015, "alt_m": 0.0},
]


def _plan(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "plan_hash": "PLAN-HASH-FOR-UPLOAD-TESTS",
        "mission_items": MISSION_ITEMS if items is None else items,
        "geofences": {
            callsign: {"polygon": POLYGON, "max_alt_m": 45.0} for callsign in CALLSIGNS
        },
    }


class Msg:
    """The subset of a pymavlink message the uploader reads."""

    def __init__(self, message_type: str, src_system: int = 201, **fields: Any) -> None:
        self._message_type = message_type
        self._src_system = src_system
        for key, value in fields.items():
            setattr(self, key, value)

    def get_type(self) -> str:
        return self._message_type

    def get_srcSystem(self) -> int:
        return self._src_system


class FakeVehicle:
    """A mission-protocol peer that stores what it was given and reports it back.

    `corrupt` is keyed by (mission type, sequence) so a lie about the mission does not
    accidentally also corrupt the fence read-back of the same aircraft.
    """

    def __init__(self, callsign: str, *, corrupt: dict[tuple[int, int], dict[str, Any]] | None = None,
                 drop_seq: tuple[int, int] | None = None,
                 param_override: dict[str, float] | None = None) -> None:
        self.callsign = callsign
        self.uploader: MissionUploader | None = None
        self.draft: dict[tuple[int, int], dict[str, Any]] = {}
        self.held: dict[int, list[dict[str, Any]]] = {}
        self.params: dict[str, float] = {}
        self.corrupt = corrupt or {}
        self.drop_seq = drop_seq
        self.param_override = param_override or {}
        self.commanded_counts: list[tuple[int, int]] = []
        self.lists_requested = 0

    # ------------------------------------------------------- GCS → vehicle wire
    def mission_count(self, target_system: int, target_component: int, count: int,
                      mission_type: int = 0) -> None:
        self.commanded_counts.append((mission_type, count))
        self.draft = {}
        for seq in range(count):
            self._emit(Msg("MISSION_REQUEST_INT", seq=seq, mission_type=mission_type))
        self._emit(Msg("MISSION_ACK", type=0, mission_type=mission_type))

    def mission_item_int(self, target_system: int, target_component: int, seq: int, frame: int,
                         command: int, current: int, autocontinue: int, p1: float, p2: float,
                         p3: float, p4: float, lat: float, lon: float, alt: float,
                         mission_type: int = 0) -> None:
        self.draft[(mission_type, seq)] = {
            "seq": seq, "frame": frame, "command": command, "current": current,
            "p1": p1, "p2": p2, "p3": p3, "p4": p4, "lat": lat, "lon": lon, "alt": alt,
        }

    def mission_request_list(self, target_system: int, target_component: int,
                             mission_type: int = 0) -> None:
        self.lists_requested += 1
        items = self.held.get(mission_type, [])
        self._emit(Msg("MISSION_COUNT", count=len(items), mission_type=mission_type))

    def mission_request_int(self, target_system: int, target_component: int, seq: int,
                            mission_type: int = 0) -> None:
        items = self.held.get(mission_type, [])
        if (mission_type, seq) == self.drop_seq or seq >= len(items):
            return  # the vehicle never answers this request
        item = dict(items[seq])
        item.update(self.corrupt.get((mission_type, seq), {}))
        self._emit(Msg(
            "MISSION_ITEM_INT", seq=item["seq"], command=item["command"], frame=item["frame"],
            x=int(round(float(item["lat"]) * 1e7)), y=int(round(float(item["lon"]) * 1e7)),
            z=float(item["alt"]), param1=item["p1"], param2=item["p2"], param3=item["p3"],
            param4=item["p4"], mission_type=mission_type,
        ))

    def param_set(self, target_system: int, target_component: int, name: str, value: float) -> None:
        self.params[name] = float(value)
        self._emit(Msg("PARAM_VALUE", param_id=name, param_value=float(value)))

    def param_request_read(self, target_system: int, target_component: int, name: str) -> None:
        value = self.param_override.get(name, self.params.get(name))
        if value is None:
            return
        self._emit(Msg("PARAM_VALUE", param_id=name, param_value=float(value)))

    # ------------------------------------------------------------- vehicle → GCS
    def _emit(self, msg: Msg) -> None:
        assert self.uploader is not None
        if msg.get_type().startswith("PARAM"):
            self.uploader.handle_param_message(self.callsign, msg)
        else:
            self.uploader.handle_mission_message(self.callsign, msg)

    def commit(self, mission_type: int) -> None:
        """Accept the draft the way a flight controller does: ordered by sequence."""
        self.held[mission_type] = [
            self.draft[(mission_type, seq)]
            for seq in sorted(s for (t, s) in self.draft if t == mission_type)
        ]


class AckingVehicle(FakeVehicle):
    """Commits the uploaded mission when it acknowledges, instead of discarding it."""

    def _emit(self, msg: Msg) -> None:
        if msg.get_type() == "MISSION_ACK" and msg.type == 0:
            self.commit(msg.mission_type)
        super()._emit(msg)


class FakeTelemetry:
    def __init__(self, fleet: list[dict[str, Any]],
                 behaviour: dict[str, dict[str, Any]] | None = None) -> None:
        behaviour = behaviour or {}
        self.transports: dict[str, FakeVehicle] = {}
        for profile in fleet:
            callsign = profile["callsign"]
            self.transports[callsign] = AckingVehicle(callsign, **behaviour.get(callsign, {}))
        self.shared_transport = None
        self.mission_message_handler = None
        self.param_message_handler = None


@pytest.fixture()
def uploader_factory(config):
    def build(behaviour: dict[str, dict[str, Any]] | None = None,
              timeouts: dict[str, float] | None = None):
        telemetry = FakeTelemetry(config.fleet, behaviour)
        uploader = MissionUploader(config, telemetry)
        for vehicle in telemetry.transports.values():
            vehicle.uploader = uploader
        if timeouts:
            # In-memory only: these are declared telemetry settings (see CONFIG_SCHEMA),
            # lowered so the timeout paths do not cost 20 s of wall clock per test.
            uploader.config.data.setdefault("telemetry", {}).update(timeouts)
        return uploader, telemetry

    return build


def _receipts(uploader_factory, behaviour=None, plan=None, timeouts=None):
    uploader, telemetry = uploader_factory(behaviour, timeouts)
    return uploader.upload_plan(plan or _plan(), "REAL"), telemetry


# --------------------------------------------------------------------- happy path
def test_faithful_vehicle_yields_verified_receipts(uploader_factory) -> None:
    receipts, telemetry = _receipts(uploader_factory)
    by_call = receipts["aircraft"]
    assert all(receipt["status"] == "VERIFIED" for receipt in by_call.values())
    assert all(receipt["simulated"] is False for receipt in by_call.values())
    assert all(receipt["evidence"] == "PENDING TEST" for receipt in by_call.values())

    ins = by_call["INS-01"]
    assert ins["mission_count"] == len(MISSION_ITEMS)
    assert ins["streams"]["mission"]["status"] == "VERIFIED"
    # Delivery aircraft fly GUIDED, so an empty mission is NOT_REQUIRED rather than a pass.
    assert by_call["DEL-01"]["streams"]["mission"]["status"] == "NOT_REQUIRED"
    assert by_call["DEL-01"]["mission_count"] == 0

    # Four unique polygon vertices plus the return point.
    assert ins["fence_count"] == len(POLYGON)
    fence = telemetry.transports["INS-01"].held[MAV_MISSION_TYPE_FENCE]
    assert [item["command"] for item in fence] == (
        [FENCE_POLYGON_INCLUSION] * (len(POLYGON) - 1) + [FENCE_RETURN_POINT]
    )
    assert all(item["p1"] == float(len(POLYGON) - 1) for item in fence[:-1])


def test_uploaded_waypoints_are_the_planned_waypoints(uploader_factory) -> None:
    _, telemetry = _receipts(uploader_factory)
    mission = telemetry.transports["INS-01"].held[MAV_MISSION_TYPE_MISSION]
    for sent, stored in zip(MISSION_ITEMS, mission):
        assert stored["command"] == sent["command"]
        assert stored["frame"] == sent["frame"]
        assert stored["lat"] == pytest.approx(sent["lat"], abs=1e-7)
        assert stored["lon"] == pytest.approx(sent["lon"], abs=1e-7)
        assert stored["alt"] == pytest.approx(sent["alt_m"], abs=1e-6)
    assert mission[0]["current"] == 1 and mission[1]["current"] == 0


def test_authored_waypoint_parameters_reach_the_vehicle(uploader_factory) -> None:
    items = [dict(MISSION_ITEMS[0]), dict(MISSION_ITEMS[1])]
    items[1]["p1"] = 12.5  # NAV_WAYPOINT hold time, seconds
    items[1]["p3"] = 3.0   # pass-through radius
    receipts, telemetry = _receipts(uploader_factory, plan=_plan(items))
    assert receipts["aircraft"]["INS-01"]["status"] == "VERIFIED"
    stored = telemetry.transports["INS-01"].held[MAV_MISSION_TYPE_MISSION][1]
    assert stored["p1"] == pytest.approx(12.5)
    assert stored["p3"] == pytest.approx(3.0)


# ------------------------------------------------------------------ dishonest peer
def test_same_count_different_waypoint_is_not_verified(uploader_factory) -> None:
    receipts, _ = _receipts(
        uploader_factory,
        {"INS-01": {"corrupt": {(MAV_MISSION_TYPE_MISSION, 1): {"lat": 12.9715}}}},  # 3.3 km north
    )
    ins = receipts["aircraft"]["INS-01"]
    assert ins["status"] == "FAILED"
    mission = ins["streams"]["mission"]
    assert mission["status"] == "FAIL"
    assert "Read-back differs" in mission["detail"]
    assert any("#1 lat" in problem for problem in mission["mismatches"])
    # The receipt detail names the failure instead of hiding behind stream labels.
    assert "Read-back differs" in ins["detail"]
    # A healthy fence on the same aircraft must not lift it to VERIFIED.
    assert ins["streams"]["fence"]["status"] == "VERIFIED"
    # The other aircraft are unaffected.
    assert receipts["aircraft"]["DEL-01"]["status"] == "VERIFIED"


def test_shifted_mission_of_the_same_length_is_not_verified(uploader_factory) -> None:
    # The vehicle stored yesterday's plan: identical count, different content.
    receipts, _ = _receipts(
        uploader_factory,
        {"INS-01": {"corrupt": {
            (MAV_MISSION_TYPE_MISSION, seq): {"alt": 25.0} for seq in range(len(MISSION_ITEMS))
        }}},
    )
    ins = receipts["aircraft"]["INS-01"]
    assert ins["streams"]["mission"]["status"] == "FAIL"
    assert all("altitude" in problem for problem in ins["streams"]["mission"]["mismatches"])


def test_int_frame_alias_from_the_vehicle_is_not_a_mismatch(uploader_factory) -> None:
    # ArduPilot may answer a GLOBAL_RELATIVE_ALT_INT (6) request with the float-frame
    # alias 3. Same frame class, same waypoint: this must still read as VERIFIED.
    receipts, _ = _receipts(
        uploader_factory,
        {"INS-01": {"corrupt": {
            (MAV_MISSION_TYPE_MISSION, seq): {"frame": 3} for seq in range(len(MISSION_ITEMS))
        }}},
    )
    assert receipts["aircraft"]["INS-01"]["status"] == "VERIFIED"


def test_a_different_frame_class_is_a_mismatch(uploader_factory) -> None:
    # 6 is relative-to-home altitude, 0 is absolute MSL: the same numbers mean a
    # different flight level, so the receipt must not call this a match.
    receipts, _ = _receipts(
        uploader_factory,
        {"INS-01": {"corrupt": {(MAV_MISSION_TYPE_MISSION, 1): {"frame": 0}}}},
    )
    ins = receipts["aircraft"]["INS-01"]
    assert ins["streams"]["mission"]["status"] == "FAIL"
    assert any("#1 frame" in problem for problem in ins["streams"]["mission"]["mismatches"])


def test_fence_and_mission_are_compared_separately(uploader_factory) -> None:
    # Corrupting only the fence must fail the fence stream and leave the mission VERIFIED.
    receipts, _ = _receipts(
        uploader_factory,
        {"DEL-01": {"corrupt": {(MAV_MISSION_TYPE_FENCE, 2): {"lon": 77.7000}}}},
    )
    del_01 = receipts["aircraft"]["DEL-01"]
    assert del_01["status"] == "FAILED"
    assert del_01["streams"]["fence"]["status"] == "FAIL"
    assert "#2 lon" in " ".join(del_01["streams"]["fence"]["mismatches"])
    assert del_01["streams"]["mission"]["status"] == "NOT_REQUIRED"
    assert del_01["streams"]["parameters"]["status"] == "VERIFIED"


def test_read_back_shorter_than_the_upload_is_not_verified(uploader_factory) -> None:
    receipts, _ = _receipts(
        uploader_factory,
        {"INS-01": {"drop_seq": (MAV_MISSION_TYPE_MISSION, 2)}},
        timeouts={"readback_timeout_s": 2.0, "upload_timeout_s": 2.0, "param_timeout_s": 2.0},
    )
    ins = receipts["aircraft"]["INS-01"]
    assert ins["status"] == "UNVERIFIED"  # the read-back never completed
    assert ins["streams"]["mission"]["status"] == "UNVERIFIED"
    assert "Read-back timed out" in ins["streams"]["mission"]["detail"]


def test_unanswered_command_uses_the_heartbeat_3s_gate_not_a_dead_socket() -> None:
    from rescueswarm.mavlink import MavlinkTransport, TransportError

    transport = MavlinkTransport(
        "udp:127.0.0.1:1", on_message=lambda msg: None, on_state=lambda n, s: None
    )
    transport.conn = object()  # a socket-like object with no verified link
    transport.connected = False
    with pytest.raises(TransportError):
        transport.mission_count(1, 1, 4, MAV_MISSION_TYPE_MISSION)
    with pytest.raises(TransportError):
        transport.param_set(1, 1, "FENCE_ENABLE", 1.0)
    with pytest.raises(TransportError):
        transport.mission_item_int(1, 1, 0, 6, 22, 1, 1, 0, 0, 0, 0, 12.94, 77.60, 40.0)


# ---------------------------------------------------------------------- parameters
def test_fence_circle_is_never_enabled_without_a_radius(uploader_factory) -> None:
    receipts, telemetry = _receipts(uploader_factory)
    params = telemetry.transports["INS-01"].params
    assert receipts["aircraft"]["INS-01"]["status"] == "VERIFIED"
    assert params["FENCE_ENABLE"] == 1.0
    # Bit 1 (circle/map) is off because the plan never authors FENCE_RADIUS: enabling it
    # would arm a breach monitor on a distance this GCS did not set or read back.
    assert params["FENCE_TYPE"] == 5.0
    assert not int(params["FENCE_TYPE"]) & 2
    assert "FENCE_RADIUS" not in params
    assert params["FENCE_ALT_MAX"] == 45.0


def test_parameter_that_the_vehicle_changed_is_not_verified(uploader_factory) -> None:
    receipts, _ = _receipts(
        uploader_factory,
        {"DEL-02": {"param_override": {"FENCE_ENABLE": 0.0}}},
    )
    del_02 = receipts["aircraft"]["DEL-02"]
    assert del_02["status"] == "PARTIAL"
    assert del_02["streams"]["parameters"]["status"] == "FAIL"
    assert "FENCE_ENABLE" in del_02["streams"]["parameters"]["detail"]


def test_fence_only_aircraft_still_gets_its_fence_verified(uploader_factory) -> None:
    receipts, telemetry = _receipts(uploader_factory)
    for callsign in ("DEL-01", "DEL-02"):
        assert receipts["aircraft"][callsign]["streams"]["fence"]["status"] == "VERIFIED"
        assert MAV_MISSION_TYPE_FENCE in telemetry.transports[callsign].held
        assert telemetry.transports[callsign].lists_requested == 1
