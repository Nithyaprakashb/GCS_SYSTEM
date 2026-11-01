"""Audit regressions for the command-addressing and evidence cluster.

These tests exist because the review found concrete ways to reach a false
"delivered" state or to command an unreachable aircraft. Each one pins the fixed
behaviour; none of them needs hardware.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from rescueswarm.config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    SAMPLES_DIR,
    safety_keys,
    validate_patch,
)
from rescueswarm.ledger import LedgerError
from rescueswarm.main import app
from rescueswarm.state import AppState
from rescueswarm.telemetry import (
    SETTLING_TX_STATES,
    TERMINAL_TX_STATES,
    CommandTransaction,
    MAV_CMD_DO_SET_SERVO,
)

SURVIVOR = {"lat": 12.9494, "lon": 77.5986, "confidence": 0.93, "frames_confirmed": 3,
            "geotag_uncertainty_m": 2.4}


def _hover_ready(state: AppState, callsign: str, lat: float, lon: float) -> None:
    for other, profile in state.telemetry.vehicles.items():
        if other == callsign:
            continue
        profile.update(
            connected=True, stale=False, identity_valid=True, armed=True, mode="AUTO",
            custom_mode=2, source="SIMULATED", data_quality="SIMULATED",
            lat=lat + 0.0015, lon=lon + 0.0015, agl_m=12.0, alt_rel_m=12.0,
            groundspeed_ms=6.0, last_seen_mono=time.monotonic(),
        )
    state.telemetry.vehicles[callsign].update(
        connected=True, stale=False, identity_valid=True, armed=True, mode="GUIDED",
        custom_mode=4, source="SIMULATED", data_quality="SIMULATED", lat=lat, lon=lon,
        agl_m=3.0, alt_rel_m=3.0, groundspeed_ms=0.0, vertical_speed_ms=0.0,
        battery_pct=90.0, last_seen_mono=time.monotonic(),
    )


@pytest.fixture()
def planned_state() -> AppState:
    """A state with an imported boundary, a plan and an initialised ledger."""
    state = AppState()
    state.mission.import_kml((SAMPLES_DIR / "sample_search_zone_10ha.kml").read_bytes(),
                             "sample_search_zone_10ha.kml", operator="pytest")
    state.mission.create_plan(operator="pytest")
    yield state
    state.db.close()


def _queued_release(state: AppState) -> dict:
    survivor = state.survivors.ingest_report("INS-01", dict(SURVIVOR), data_quality="SIMULATED")
    assert survivor is not None
    vehicles = {
        "DEL-01": {"callsign": "DEL-01", "lat": 12.9495, "lon": 77.5987, "connected": True,
                   "stale": False, "identity_valid": True, "battery_pct": 90.0,
                   "link_quality": 95, "task": "STANDBY", "armed": False,
                   "groundspeed_ms": 0.0, "agl_m": 0.0, "failsafe": None}
    }
    decision = state.allocator.allocate(survivor["survivor_id"], vehicles)
    assert decision["allocated"] is True, decision
    assignment = decision["assignment"]
    state.delivery.create(assignment)
    _hover_ready(state, assignment["callsign"], survivor["drop_lat"], survivor["drop_lon"])
    for target in ("LAUNCHING", "TRANSIT", "DESCENT", "HOVER_VERIFY"):
        state.delivery.transition(assignment["assignment_id"], target, detail="pytest")
    return {"survivor": survivor, "assignment": assignment}


# ------------------------------------------------------------------ addressing
def test_real_mode_refuses_to_address_a_vehicle_it_cannot_see(planned_state: AppState) -> None:
    state = planned_state
    state.telemetry.mode = "REAL"
    state.telemetry.connected = True
    tx = state.telemetry.send_command("DEL-01", MAV_CMD_DO_SET_SERVO,
                                      [9.0, 1900.0, 0, 0, 0, 0, 0], "release")
    assert tx.state == "REJECTED"
    assert "NOT_ADDRESSED" in tx.detail
    assert state.telemetry.transaction(tx.tx_id)["state"] == "REJECTED"


def test_real_mode_gate_needs_mavlink_not_cached_simulation(planned_state: AppState) -> None:
    state = planned_state
    state.telemetry.sim_heartbeat("DEL-01")
    state.telemetry.mode = "REAL"
    # A simulated heartbeat is not evidence of a radio link, so the gate must fail.
    assert state.telemetry.all_fresh() is False
    assert "DEL-01" in state.telemetry.freshness_report()


def test_switching_to_real_wipes_simulated_vehicle_records(planned_state: AppState) -> None:
    state = planned_state

    async def scenario() -> None:
        await state.telemetry.connect("SIMULATION")
        assert state.telemetry.all_fresh() is True
        with pytest.raises(Exception):
            # No endpoint is configured, so a REAL connect must fail rather than
            # report a link that has never carried a heartbeat.
            await state.telemetry.connect("REAL")
        assert state.telemetry.all_fresh() is False
        assert all(v["source"] != "SIMULATED" for v in state.telemetry.vehicles.values())
        assert all(v["identity_valid"] is False for v in state.telemetry.vehicles.values())

    asyncio.run(scenario())


# ---------------------------------------------------------------- ACK lifecycle
def test_release_waits_for_the_ack_of_its_own_command(planned_state: AppState) -> None:
    state = planned_state
    ctx = _queued_release(state)
    assignment = ctx["assignment"]
    # The simulator is not running, so nothing ACKs: the task must hold at ACK_PENDING
    # and the kit must not be marked away.
    state.telemetry.simulate_ack = lambda tx_id: None  # type: ignore[assignment]
    result = state.delivery.request_release(assignment["assignment_id"], hover_verified=True)
    assert result["settled"] is False
    assert state.delivery.get(assignment["assignment_id"])["state"] == "ACK_PENDING"
    assert state.ledger.kits[assignment["kit_id"]]["load_status"] == "RESERVED"

    # A late ACK settles it exactly once.
    tx_id = state.delivery.get(assignment["assignment_id"])["pending_tx_id"]
    tx = state.telemetry._transactions[tx_id]
    tx.state = "ACKNOWLEDGED"
    tx.ack_at = "2026-01-01T00:00:00Z"
    first = state.delivery.settle_release_transaction(tx_id)
    second = state.delivery.settle_release_transaction(tx_id)
    assert first["state"] == "RELEASE_SENT"
    assert second.get("already_settled") is True
    assert state.ledger.kits[assignment["kit_id"]]["load_status"] == "RELEASE_SENT"


def test_unanswered_release_times_out_and_returns_the_kit(planned_state: AppState) -> None:
    state = planned_state
    ctx = _queued_release(state)
    assignment = ctx["assignment"]
    state.telemetry.simulate_ack = lambda tx_id: None  # type: ignore[assignment]
    state.delivery.request_release(assignment["assignment_id"], hover_verified=True)
    tx_id = state.delivery.get(assignment["assignment_id"])["pending_tx_id"]
    state.telemetry._transactions[tx_id].timeout_at = time.monotonic() - 1
    state.telemetry._sweep_transactions(time.monotonic())
    assert state.telemetry.transaction(tx_id)["state"] == "TIMEOUT"
    # Never acknowledged, so the kit is still aboard: back to the pool, survivor unserved.
    kit = state.ledger.kits[assignment["kit_id"]]
    assert kit["load_status"] == "LOADED"
    assert kit["reserved_for"] is None
    assert state.delivery.get(assignment["assignment_id"])["state"] == "ABORTED"
    assert state.survivors.get(ctx["survivor"]["survivor_id"])["status"] == "UNSERVED"


def test_command_ack_from_a_non_autopilot_component_is_ignored(planned_state: AppState) -> None:
    state = planned_state
    ctx = _queued_release(state)
    assignment = ctx["assignment"]
    state.telemetry.simulate_ack = lambda tx_id: None  # type: ignore[assignment]
    state.delivery.request_release(assignment["assignment_id"], hover_verified=True)
    tx_id = state.delivery.get(assignment["assignment_id"])["pending_tx_id"]

    class CompanionAck:
        command = MAV_CMD_DO_SET_SERVO
        result = 0
        severity = 6
        text = ""

        def get_srcComponent(self) -> int:  # noqa: N802 - pymavlink naming
            return 100  # camera component

    state.telemetry._on_command_ack(assignment["callsign"], CompanionAck())
    assert state.telemetry.transaction(tx_id)["state"] == "SENT"
    assert state.delivery.get(assignment["assignment_id"])["state"] == "ACK_PENDING"


# ------------------------------------------------------- verification resolves
# The audit found that ACKNOWLEDGED sat in TERMINAL_TX_STATES, so the sweep skipped it and
# the ACKNOWLEDGED -> VERIFIED / ACK_UNVERIFIED resolution could never run: every accepted
# command froze at "accepted" forever. These pin the corrected lifecycle.

def _ack_tx(state: AppState, *, expectation: dict | None, deadline_in_s: float = 10.0) -> CommandTransaction:
    tx = CommandTransaction(
        tx_id="TX-VERIFY", callsign="DEL-01", command=22, label="Take off DEL-01",
        params=[0, 0, 0, 0, 12.95, 77.60, 15.0], state="ACKNOWLEDGED",
        ack_result=0, ack_at="2026-01-01T00:00:00Z", simulated=False,
        expectation=expectation, verify_deadline=time.monotonic() + deadline_in_s,
    )
    state.telemetry._transactions[tx.tx_id] = tx
    return tx


def test_acknowledged_is_not_terminal_and_still_settles_the_release_engine() -> None:
    # ACKNOWLEDGED must stay open for verification, yet still be the moment the payload
    # ledger reacts to — those two ideas must not be expressed by the same set.
    assert "ACKNOWLEDGED" not in TERMINAL_TX_STATES
    assert "ACKNOWLEDGED" in SETTLING_TX_STATES


def test_accepted_command_becomes_verified_only_when_vehicle_state_confirms(
    planned_state: AppState,
) -> None:
    state = planned_state
    state.telemetry.vehicles["DEL-01"].update(alt_rel_m=1.0, armed=True, custom_mode=3, mode="AUTO")
    tx = _ack_tx(state, expectation={"kind": "altitude_above_m", "value": 12.0, "describe": "12 m"})

    state.telemetry._sweep_transactions(time.monotonic())
    assert tx.state == "ACKNOWLEDGED", "still below the commanded altitude"

    state.telemetry.vehicles["DEL-01"]["alt_rel_m"] = 15.0
    state.telemetry._sweep_transactions(time.monotonic())
    assert tx.state == "VERIFIED"
    assert tx.verified_at is not None
    assert tx.verify_deadline is None


def test_accepted_command_becomes_ack_unverified_when_the_state_never_arrives(
    planned_state: AppState,
) -> None:
    state = planned_state
    state.telemetry.vehicles["DEL-01"].update(alt_rel_m=1.0)
    tx = _ack_tx(state, expectation={"kind": "altitude_above_m", "value": 12.0,
                                     "describe": "12 m"}, deadline_in_s=-1.0)

    state.telemetry._sweep_transactions(time.monotonic())
    assert tx.state == "ACK_UNVERIFIED"
    assert "NOT confirmed" in tx.detail
    assert tx.state in TERMINAL_TX_STATES


def test_command_without_a_state_check_stays_acknowledged_not_verified(
    planned_state: AppState,
) -> None:
    # A servo release has no vehicle-state check. It must not silently read as VERIFIED.
    state = planned_state
    tx = _ack_tx(state, expectation=None)
    state.telemetry._sweep_transactions(time.monotonic())
    state.telemetry._sweep_transactions(time.monotonic() + 999)
    assert tx.state == "ACKNOWLEDGED"


def test_link_change_labels_an_accepted_command_by_what_actually_happened(
    planned_state: AppState,
) -> None:
    state = planned_state
    accepted = _ack_tx(state, expectation={"kind": "armed", "value": True, "describe": "armed"})
    never_answered = CommandTransaction(
        tx_id="TX-SILENT", callsign="INS-01", command=20, label="Return INS-01",
        params=[0] * 7, state="SENT", timeout_at=time.monotonic() + 30,
    )
    state.telemetry._transactions[never_answered.tx_id] = never_answered

    state.telemetry._abandon_pending_transactions("Link mode changed")
    assert accepted.state == "ACK_UNVERIFIED", "an ACK was received; TIMEOUT would be a lie"
    assert never_answered.state == "TIMEOUT"


def test_release_frame_that_left_the_station_is_never_counted_back_in(
    planned_state: AppState,
) -> None:
    """CRITICAL ledger integrity.

    ACK_PENDING means the servo frame was already emitted. If the answer never comes back,
    the kit's fate is unknown — returning it to the magazine would let a second survivor be
    promised hardware that may already be on the ground.
    """
    state = planned_state
    ctx = _queued_release(state)
    assignment = ctx["assignment"]
    state.telemetry.simulate_ack = lambda tx_id: None  # type: ignore[assignment]
    state.delivery.request_release(assignment["assignment_id"], hover_verified=True)
    tx_id = state.delivery.get(assignment["assignment_id"])["pending_tx_id"]
    tx = state.telemetry._transactions[tx_id]
    tx.transmitted = True  # what a successful transport write sets in REAL mode
    tx.timeout_at = time.monotonic() - 1
    state.telemetry._sweep_transactions(time.monotonic())

    assert tx.state == "TIMEOUT"
    kit = state.ledger.kits[assignment["kit_id"]]
    assert kit["load_status"] == "UNKNOWN", "transmitted frame must not re-enter the magazine"
    assert kit["final_status"] == "UNKNOWN"
    assert state.delivery.get(assignment["assignment_id"])["state"] == "UNKNOWN"
    allowed, reason = state.ledger.release_allowed(assignment["kit_id"], ctx["survivor"]["survivor_id"])
    assert allowed is False and "terminal" in reason.lower()


def test_abandoned_reservation_is_marked_unknown_instead_of_silently_kept(
    planned_state: AppState,
) -> None:
    # A command attempt that failed mid-write leaves the reservation in place. The ledger
    # used to accept that and do nothing, so the task said UNKNOWN while the kit said LOADED.
    state = planned_state
    ctx = _queued_release(state)
    assignment = ctx["assignment"]
    assert state.ledger.kits[assignment["kit_id"]]["load_status"] == "RESERVED"
    state.ledger.note_command_abandoned(assignment["kit_id"], reason="command attempt failed")
    kit = state.ledger.kits[assignment["kit_id"]]
    assert kit["load_status"] == "UNKNOWN" and kit["final_status"] == "UNKNOWN"


# ------------------------------------------------------------- link routing
class _FakeHeartbeat:
    """Minimal heartbeat stand-in: pymavlink exposes these attributes by name."""

    def __init__(self, sysid: int) -> None:
        self.sysid = sysid

    def get_type(self) -> str:  # noqa: N802 - pymavlink naming
        return "HEARTBEAT"

    def get_srcSystem(self) -> int:  # noqa: N802 - pymavlink naming
        return self.sysid

    base_mode = 1
    custom_mode = 4  # GUIDED
    autopilot = 3    # MAV_AUTOPILOT_ARDUPILOTMEGA
    type = 0         # accepted for any role


def test_independent_modem_still_processes_its_own_aircraft(planned_state: AppState) -> None:
    """CRITICAL hardware mapping.

    One blanket `if endpoint_callsign: return` dropped every message on a per-aircraft
    modem, including traffic from the vehicle that modem belongs to: independent topology
    looked commissioned and was dead.
    """
    state = planned_state
    state.telemetry._on_mavlink("DEL-01", _FakeHeartbeat(sysid=2))
    vehicle = state.telemetry.vehicles["DEL-01"]
    assert vehicle["source"] == "MAVLINK"
    assert vehicle["connected"] is True and vehicle["identity_valid"] is True
    assert vehicle["data_quality"] == "LIVE"


def test_independent_modem_still_rejects_the_wrong_aircraft(planned_state: AppState) -> None:
    state = planned_state
    state.telemetry._on_mavlink("DEL-01", _FakeHeartbeat(sysid=1))  # INS-01 on DEL-01's modem
    assert state.telemetry.vehicles["DEL-01"]["source"] != "MAVLINK"
    assert state.telemetry.vehicles["INS-01"]["source"] != "MAVLINK"
    interlocks = [event for event in state.bus.recent(20) if event["topic"] == "safety.interlock"]
    assert any(event["payload"]["rule_id"] == "ENDPOINT_MISMATCH" for event in interlocks)


# ---------------------------------------------------------------- recall gate
def test_recall_blocks_a_payload_release(planned_state: AppState) -> None:
    """Emergency recall must outrank a delivery that is already at hover."""
    state = planned_state
    ctx = _queued_release(state)
    assignment = ctx["assignment"]
    state.safety.set_recall(True, "pytest recall")
    result = state.delivery.request_release(assignment["assignment_id"], hover_verified=True)
    assert result["sent"] is False
    assert "recall" in result["reason"].lower()
    failed = {check["check_id"] for check in result["clearance"]["checks"] if not check["ok"]}
    assert "recall_active" in failed
    assert state.ledger.kits[assignment["kit_id"]]["load_status"] == "RESERVED"


# -------------------------------------------------------------------- evidence
def test_beam_evidence_without_release_is_refused(planned_state: AppState) -> None:
    state = planned_state
    ctx = _queued_release(state)
    assignment = ctx["assignment"]
    with pytest.raises(LedgerError):
        state.ledger.apply_beam_result(assignment["kit_id"], "CONFIRMED", event_id="EV-X")
    assert state.ledger.kits[assignment["kit_id"]]["final_status"] is None


def test_abort_answers_with_a_body_and_leaves_no_kit_reserved(planned_state: AppState) -> None:
    state = planned_state
    ctx = _queued_release(state)
    assignment = ctx["assignment"]
    release = state.delivery.request_release(assignment["assignment_id"], hover_verified=True)
    assert release["sent"] is True, release
    state.mission.phase = "EXECUTING"
    status = state.mission.abort(operator="pytest", reason="regression")
    assert isinstance(status, dict), "abort must answer with a body, not None"
    assert status["phase"] == "ABORTED"
    # The kit left the magazine with no beam evidence: UNKNOWN, never re-fired.
    assert state.ledger.kits[assignment["kit_id"]]["final_status"] == "UNKNOWN"
    assert status["kits_unverified"] == [assignment["kit_id"]]
    assert status["kits_returned_to_magazine"] == []
    assert all(kit["load_status"] != "RESERVED" for kit in state.ledger.kits.values())
    allowed, reason = state.ledger.release_allowed(assignment["kit_id"], ctx["survivor"]["survivor_id"])
    assert allowed is False and "terminal" in reason.lower()


def test_recall_clear_resumes_only_the_simulator(planned_state: AppState) -> None:
    state = planned_state
    state.safety.set_recall(True, "regression")
    state.mission.recall = True
    result = state.mission.clear_recall(operator="pytest")
    assert result["recall"] is False
    assert result["simulator_resumed"] is False  # mission mode is SIMULATION but no sim run
    assert state.safety.recall_active is False


# ---------------------------------------------------------------------- config
def test_safety_thresholds_cannot_be_relaxed_through_the_api() -> None:
    current = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    for patch in (
        {"mission": {"descent_clearance_m": 0.01}},
        {"mission": {"descent_clearance_m": -50}},
        {"mission": {"max_survivors": "banana"}},
        {"mission": {"not_a_setting_this_build_reads": 5}},
        {"telemetry": {"topology_verified": True}},
        {"nonexistent": {"x": 1}},
    ):
        with pytest.raises(ConfigError):
            validate_patch(patch, current)


def test_shipped_but_undeclared_setting_stays_patchable_with_type_checks() -> None:
    current = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    clean = validate_patch({"mission": {"proximity_warn_m": 8.0}}, current)
    assert clean["mission"]["proximity_warn_m"] == 8.0
    with pytest.raises(ConfigError):
        validate_patch({"mission": {"proximity_warn_m": "eight"}}, current)


def test_undeclared_flight_settings_are_still_safety_gated() -> None:
    patch = {"mission": {"proximity_warn_m": 8.0}, "telemetry": {"command_timeout_s": 3.0}}
    assert "mission.proximity_warn_m" in safety_keys(patch)
    assert "telemetry.command_timeout_s" in safety_keys(patch)


@pytest.fixture()
def http_client():
    with TestClient(app) as test_client:
        yield test_client


def test_config_endpoint_refuses_a_protected_key(http_client: TestClient) -> None:
    response = http_client.patch("/api/system/config",
                                 json={"patch": {"telemetry": {"topology_verified": True}}})
    assert response.status_code == 400
    assert "not operator-settable" in response.json()["detail"]
    config = Config()
    assert config.get("telemetry", "topology_verified") is False


def test_config_endpoint_refuses_a_safety_relaxation(http_client: TestClient) -> None:
    response = http_client.patch("/api/system/config",
                                 json={"patch": {"mission": {"descent_clearance_m": 0.5}}})
    assert response.status_code == 400
    assert "between" in response.json()["detail"]
    assert Config().get("mission", "descent_clearance_m") == 20.0
