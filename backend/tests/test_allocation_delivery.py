"""Deterministic allocation, atomic kit reservation and delivery lifecycle."""

from __future__ import annotations

import time

import pytest

from rescueswarm.delivery import DeliveryEngine
from rescueswarm.ledger import LedgerError
from rescueswarm.state import AppState

SURVIVOR = {"lat": 12.9494, "lon": 77.5986, "confidence": 0.93, "frames_confirmed": 3,
            "geotag_uncertainty_m": 2.4}


def _delivery_vehicle(callsign: str, lat: float, lon: float, battery: float = 90.0,
                      connected: bool = True) -> dict:
    return {
        "callsign": callsign, "lat": lat, "lon": lon, "connected": connected,
        "stale": False, "identity_valid": True, "battery_pct": battery,
        "link_quality": 95, "task": "STANDBY", "armed": False,
        "groundspeed_ms": 0.0, "agl_m": 0.0, "failsafe": None,
    }


def _report(state: AppState) -> dict:
    survivor = state.survivors.ingest_report("INS-01", dict(SURVIVOR), data_quality="SIMULATED")
    assert survivor is not None
    return survivor


def _hover_ready(state: AppState, callsign: str, lat: float, lon: float) -> None:
    """Put the cached vehicle records in the state the release gate expects.

    Every aircraft needs a fresh position: the clearance engine refuses a drop when it
    cannot see the rest of the fleet, so a test that only positions one vehicle must fail.
    """
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
        connected=True, stale=False, identity_valid=True, identity_note="test",
        armed=True, mode="GUIDED", custom_mode=4, source="SIMULATED",
        data_quality="SIMULATED", lat=lat, lon=lon, agl_m=3.0, alt_rel_m=3.0,
        groundspeed_ms=0.0, vertical_speed_ms=0.0, heading_deg=0.0, battery_pct=90.0,
        last_seen_mono=time.monotonic(), last_heartbeat="2026-01-01T00:00:00Z",
    )


def test_lowest_eta_wins_and_reserves_one_kit(mission_state: AppState) -> None:
    state = mission_state
    survivor = _report(state)
    vehicles = {
        "DEL-01": _delivery_vehicle("DEL-01", 12.9514, 77.5986),  # ~220 m away
        "DEL-02": _delivery_vehicle("DEL-02", 12.9495, 77.5987),  # on top of the survivor
    }
    decision = state.allocator.allocate(survivor["survivor_id"], vehicles)
    assert decision["allocated"] is True
    assignment = decision["assignment"]
    assert assignment["callsign"] == "DEL-02"
    assert assignment["kit_id"] == "DEL-02-K01"
    assert assignment["status"] == "ASSIGNED"
    assert state.ledger.reserved_for(survivor["survivor_id"])["kit_id"] == "DEL-02-K01"
    assert state.ledger.free_count("DEL-02") == 4

    # A second allocation for the same survivor must not reserve a second kit.
    again = state.allocator.allocate(survivor["survivor_id"], vehicles)
    assert again["allocated"] is False
    assert state.ledger.free_count("DEL-02") == 4


def test_aircraft_without_free_kit_is_rejected(mission_state: AppState) -> None:
    state = mission_state
    for index in range(5):
        state.ledger.reserve("DEL-02", f"SYNTH-{index}")
    survivor = _report(state)
    vehicles = {
        "DEL-01": _delivery_vehicle("DEL-01", 12.9514, 77.5986),
        "DEL-02": _delivery_vehicle("DEL-02", 12.9495, 77.5987),
    }
    decision = state.allocator.allocate(survivor["survivor_id"], vehicles)
    assert decision["allocated"] is True
    assert decision["assignment"]["callsign"] == "DEL-01"
    rejected = {item["callsign"]: item["reasons"] for item in decision["rejections"]}
    assert "No free kit" in rejected["DEL-02"]


def test_offline_aircraft_is_rejected(mission_state: AppState) -> None:
    state = mission_state
    survivor = _report(state)
    vehicles = {
        "DEL-01": _delivery_vehicle("DEL-01", 12.9495, 77.5987, connected=False),
        "DEL-02": _delivery_vehicle("DEL-02", 12.9514, 77.5986),
    }
    decision = state.allocator.allocate(survivor["survivor_id"], vehicles)
    assert decision["assignment"]["callsign"] == "DEL-02"
    assert any(item["callsign"] == "DEL-01" for item in decision["rejections"])


def test_delivered_status_survives_parking(mission_state: AppState) -> None:
    state = mission_state
    survivor = _report(state)
    vehicles = {
        "DEL-01": _delivery_vehicle("DEL-01", 12.9495, 77.5987),
        "DEL-02": _delivery_vehicle("DEL-02", 12.9514, 77.5986),
    }
    assignment = state.allocator.allocate(survivor["survivor_id"], vehicles)["assignment"]
    task = state.delivery.create(assignment)
    _hover_ready(state, assignment["callsign"], survivor["drop_lat"], survivor["drop_lon"])

    for target in ("LAUNCHING", "TRANSIT", "DESCENT", "HOVER_VERIFY"):
        state.delivery.transition(task["assignment_id"], target, detail="pytest")

    # The release command must be ACKed before the machine reaches RELEASE_SENT.
    released = state.delivery.request_release(task["assignment_id"], hover_verified=True)
    assert released["sent"] is True, released
    assert state.delivery.get(task["assignment_id"])["state"] == "RELEASE_SENT"

    beam = state.delivery.apply_beam_result(task["assignment_id"], "CONFIRMED",
                                            event_id="EV-TEST-CONFIRM-1")
    assert beam["confirmed"] is True
    assert state.delivery.get(task["assignment_id"])["state"] == "CONFIRMED"
    assert state.allocator.assignments[task["assignment_id"]]["status"] == "DELIVERED"
    assert state.survivors.get(survivor["survivor_id"])["status"] == "DELIVERED"

    for target in ("CLIMB", "RETURNING", "STANDBY"):
        state.delivery.transition(task["assignment_id"], target, detail="pytest")
    # Parking the aircraft must not downgrade the operator-facing badge.
    assert state.allocator.assignments[task["assignment_id"]]["status"] == "DELIVERED"


def test_illegal_transition_is_refused(mission_state: AppState) -> None:
    state = mission_state
    survivor = _report(state)
    vehicles = {"DEL-01": _delivery_vehicle("DEL-01", 12.9495, 77.5987)}
    assignment = state.allocator.allocate(survivor["survivor_id"], vehicles)["assignment"]
    task = state.delivery.create(assignment)
    with pytest.raises(ValueError):
        state.delivery.transition(task["assignment_id"], "CONFIRMED", detail="skip states")


def test_release_cannot_be_commanded_from_the_wrong_state(mission_state: AppState) -> None:
    """A queued task may not fire a servo; only HOVER_VERIFY is release-eligible."""
    state = mission_state
    survivor = _report(state)
    vehicles = {"DEL-01": _delivery_vehicle("DEL-01", 12.9495, 77.5987)}
    assignment = state.allocator.allocate(survivor["survivor_id"], vehicles)["assignment"]
    task = state.delivery.create(assignment)
    _hover_ready(state, "DEL-01", survivor["drop_lat"], survivor["drop_lon"])
    result = state.delivery.request_release(task["assignment_id"], hover_verified=True)
    assert result["sent"] is False
    assert "not release-eligible" in result["reason"] or "Release refused" in result["reason"]
    assert state.delivery.get(task["assignment_id"])["state"] == "QUEUED"
    # Nothing left the GCS, so the kit is still reserved and still aboard.
    assert state.ledger.kits[assignment["kit_id"]]["load_status"] == "RESERVED"


def test_double_release_is_blocked_by_ledger(mission_state: AppState) -> None:
    state = mission_state
    survivor = _report(state)
    vehicles = {"DEL-01": _delivery_vehicle("DEL-01", 12.9495, 77.5987)}
    assignment = state.allocator.allocate(survivor["survivor_id"], vehicles)["assignment"]
    task = state.delivery.create(assignment)
    kit_id = assignment["kit_id"]

    # A kit that never had a release commanded cannot be marked delivered.
    with pytest.raises(LedgerError):
        state.ledger.apply_beam_result(kit_id, "CONFIRMED", event_id="EV-TEST-PHANTOM")
    assert state.ledger.kits[kit_id]["final_status"] is None

    _hover_ready(state, "DEL-01", survivor["drop_lat"], survivor["drop_lon"])
    for target in ("LAUNCHING", "TRANSIT", "DESCENT", "HOVER_VERIFY"):
        state.delivery.transition(task["assignment_id"], target, detail="pytest")
    assert state.delivery.request_release(task["assignment_id"], hover_verified=True)["sent"] is True
    assert state.delivery.apply_beam_result(task["assignment_id"], "CONFIRMED",
                                           event_id="EV-TEST-REAL")["confirmed"] is True

    allowed, reason = state.ledger.release_allowed(kit_id, survivor["survivor_id"])
    assert allowed is False
    assert "terminal" in reason.lower()
    # Re-playing the same evidence id must not create a second record.
    with pytest.raises(LedgerError):
        state.ledger.apply_beam_result(kit_id, "CONFIRMED", event_id="EV-TEST-REAL")


def test_beam_evidence_requires_an_event_id(mission_state: AppState) -> None:
    """Unattributable evidence is refused: silent proof is not proof."""
    state = mission_state
    survivor = _report(state)
    vehicles = {"DEL-01": _delivery_vehicle("DEL-01", 12.9495, 77.5987)}
    assignment = state.allocator.allocate(survivor["survivor_id"], vehicles)["assignment"]
    task = state.delivery.create(assignment)
    _hover_ready(state, "DEL-01", survivor["drop_lat"], survivor["drop_lon"])
    for target in ("LAUNCHING", "TRANSIT", "DESCENT", "HOVER_VERIFY"):
        state.delivery.transition(task["assignment_id"], target, detail="pytest")
    assert state.delivery.request_release(task["assignment_id"], hover_verified=True)["sent"] is True
    with pytest.raises(LedgerError):
        state.delivery.apply_beam_result(task["assignment_id"], "CONFIRMED")
    assert state.ledger.kits[assignment["kit_id"]]["final_status"] is None


def test_second_reservation_for_same_survivor_raises(mission_state: AppState) -> None:
    state = mission_state
    survivor = _report(state)
    state.ledger.reserve("DEL-01", survivor["survivor_id"])
    with pytest.raises(LedgerError):
        state.ledger.reserve("DEL-02", survivor["survivor_id"])
