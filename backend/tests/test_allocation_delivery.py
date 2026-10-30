"""Deterministic allocation, atomic kit reservation and delivery lifecycle."""

from __future__ import annotations

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

    for target in ("LAUNCHING", "TRANSIT", "DESCENT", "HOVER_VERIFY", "RELEASE_SENT", "CONFIRMED"):
        state.delivery.transition(task["assignment_id"], target, detail="pytest")
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


def test_double_release_is_blocked_by_ledger(mission_state: AppState) -> None:
    state = mission_state
    survivor = _report(state)
    vehicles = {"DEL-01": _delivery_vehicle("DEL-01", 12.9495, 77.5987)}
    assignment = state.allocator.allocate(survivor["survivor_id"], vehicles)["assignment"]
    state.delivery.create(assignment)
    kit = state.ledger.reserved_for(survivor["survivor_id"])
    assert state.ledger.apply_beam_result(kit["kit_id"], "CONFIRMED")["final_status"] == "DELIVERED"
    allowed, reason = state.ledger.release_allowed(kit["kit_id"], survivor["survivor_id"])
    assert allowed is False
    assert "terminal" in reason.lower()


def test_second_reservation_for_same_survivor_raises(mission_state: AppState) -> None:
    state = mission_state
    survivor = _report(state)
    state.ledger.reserve("DEL-01", survivor["survivor_id"])
    with pytest.raises(LedgerError):
        state.ledger.reserve("DEL-02", survivor["survivor_id"])
