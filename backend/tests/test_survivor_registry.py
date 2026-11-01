"""Survivor registry limits, metric hygiene and merge authority.

The registry is where an onboard detector's words become a kit demand, so these
tests pin the honesty rules: an implausible field is reported as implausible, a
capacity ceiling is enforced rather than merely displayed, and a report never
moves a geotag an aircraft is already flying to.
"""

from __future__ import annotations

import math

import pytest

from rescueswarm.state import AppState

# Spread across the 10 ha sample zone, each pair far beyond merge_distance_m.
GRID = [
    (12.9495, 77.5985),
    (12.9495, 77.6005),
    (12.9505, 77.5985),
    (12.9505, 77.6005),
]


def _report(lat: float, lon: float, **overrides) -> dict:
    envelope = {"lat": lat, "lon": lon, "confidence": 0.93, "frames_confirmed": 3,
                "geotag_uncertainty_m": 2.4}
    envelope.update(overrides)
    return envelope


def _set_capacity(state: AppState, cap: int) -> None:
    # In-memory only: writing settings.json would leak into other test fixtures.
    state.config.data.setdefault("mission", {})["max_survivors"] = cap


# ------------------------------------------------------------------------ capacity
def test_registry_stops_at_the_configured_capacity(mission_state: AppState) -> None:
    state = mission_state
    _set_capacity(state, 2)
    first = state.survivors.ingest_report("INS-01", _report(*GRID[0]), data_quality="SIMULATED")
    second = state.survivors.ingest_report("INS-01", _report(*GRID[1]), data_quality="SIMULATED")
    assert first and second
    third = state.survivors.ingest_report("INS-01", _report(*GRID[2]), data_quality="SIMULATED")
    assert third is None
    assert len(state.survivors.list()) == 2
    rejected = [event for event in state.bus.recent(50)
                if event["topic"] == "survivor.rejected"]
    assert "capacity" in rejected[-1]["payload"]["reason"]
    assert rejected[-1]["data_quality"] == "SIMULATED"


def test_capacity_does_not_block_a_merge_of_a_known_case(mission_state: AppState) -> None:
    state = mission_state
    _set_capacity(state, 1)
    assert state.survivors.ingest_report("INS-01", _report(*GRID[0])) is not None
    # 1.5 m away: the same person, so the registry must not refuse it as "new".
    merged = state.survivors.ingest_report(
        "INS-01", _report(GRID[0][0] + 0.0000135, GRID[0][1])
    )
    assert merged is not None
    assert merged["observations"] == 2
    assert len(state.survivors.list()) == 1


# --------------------------------------------------------------------- field hygiene
def test_non_finite_confidence_is_rejected(mission_state: AppState) -> None:
    state = mission_state
    for bad in (float("nan"), float("inf"), "high"):
        assert state.survivors.ingest_report("INS-01", _report(*GRID[0], confidence=bad)) is None
    assert state.survivors.list() == []


def test_absurd_uncertainty_is_carried_as_the_cap_not_silently_improved(
    mission_state: AppState,
) -> None:
    state = mission_state
    survivor = state.survivors.ingest_report(
        "INS-01", _report(*GRID[0], geotag_uncertainty_m=400.0), data_quality="SIMULATED"
    )
    assert survivor is not None
    # 400 m of doubt cannot become 3 m of doubt; the registry reports the worst bound
    # it will carry and tells the operator the field was implausible.
    assert survivor["uncertainty_m"] == state.survivors.MAX_UNCERTAINTY_M
    rejected = [event for event in state.bus.recent(50) if event["topic"] == "survivor.rejected"]
    assert "mpausible geotag uncertainty" in rejected[-1]["payload"]["reason"]


def test_uncertainty_shrinks_only_with_better_evidence(mission_state: AppState) -> None:
    state = mission_state
    survivor = state.survivors.ingest_report(
        "INS-01", _report(*GRID[0], geotag_uncertainty_m=20.0)
    )
    again = state.survivors.ingest_report("INS-01", _report(*GRID[0], geotag_uncertainty_m=1.0))
    assert again["uncertainty_m"] == 1.0
    worse = state.survivors.ingest_report("INS-01", _report(*GRID[0], geotag_uncertainty_m=9.0))
    assert worse["uncertainty_m"] == 1.0
    assert worse["observations"] == 3
    assert survivor["survivor_id"] == again["survivor_id"]


# ------------------------------------------------------------------- merge authority
def test_a_merge_never_moves_a_geotag_an_aircraft_is_committed_to(
    mission_state: AppState,
) -> None:
    state = mission_state
    survivor = state.survivors.ingest_report("INS-01", _report(*GRID[0]))
    state.survivors.set_status(survivor["survivor_id"], "ASSIGNED", owner="DEL-01",
                               kit_id="DEL-01-K01")
    # A second detection 4 m east would, if merged freely, re-aim a live drop.
    merged = state.survivors.ingest_report(
        "INS-01", _report(GRID[0][0], GRID[0][1] + 0.0000367)
    )
    assert merged is not None
    assert merged["observations"] == 2
    assert merged["lat"] == survivor["lat"]
    assert merged["lon"] == survivor["lon"]
    assert merged["survivor_id"] == survivor["survivor_id"]
    updated = [event for event in state.bus.recent(50)
               if event["topic"] == "survivor.updated" and event["payload"].get("merged")]
    assert updated[-1]["payload"]["geotag_locked"] is True


def test_a_confirmed_case_still_moves_toward_the_weighted_fix(mission_state: AppState) -> None:
    state = mission_state
    first = state.survivors.ingest_report("INS-01", _report(*GRID[0]))
    second = state.survivors.ingest_report(
        "INS-01", _report(GRID[0][0], GRID[0][1] + 0.0000367, confidence=0.99)
    )
    assert second["lon"] != first["lon"]
    assert first["lon"] < second["lon"] < GRID[0][1] + 0.0000367
    assert not math.isinf(second["lon"])


# ----------------------------------------------------------------------- status gate
def test_delivered_outcome_is_not_reopened_by_a_late_report(mission_state: AppState) -> None:
    state = mission_state
    survivor = state.survivors.ingest_report("INS-01", _report(*GRID[0]))
    state.survivors.set_status(survivor["survivor_id"], "DELIVERED")
    with pytest.raises(ValueError):
        state.survivors.set_status(survivor["survivor_id"], "CONFIRMED")
    # Only an evidence reconciliation may downgrade it, and it goes to UNKNOWN.
    assert state.survivors.set_status(survivor["survivor_id"], "UNKNOWN")["status"] == "UNKNOWN"


def test_unknown_status_and_unknown_survivor_are_refused(mission_state: AppState) -> None:
    state = mission_state
    survivor = state.survivors.ingest_report("INS-01", _report(*GRID[0]))
    with pytest.raises(ValueError):
        state.survivors.set_status(survivor["survivor_id"], "SOMETHING_ELSE")
    with pytest.raises(KeyError):
        state.survivors.set_status("S-99", "ASSIGNED")
    assert len(state.survivors.list()) == 1


def test_unserved_case_can_be_re_allocated(mission_state: AppState) -> None:
    state = mission_state
    survivor = state.survivors.ingest_report("INS-01", _report(*GRID[0]))
    state.survivors.set_status(survivor["survivor_id"], "UNSERVED", note="no kit")
    # The Mission Screen promises an automatic retry while the sortie runs, so the
    # registry must let that happen instead of treating UNSERVED as terminal.
    assert state.survivors.set_status(survivor["survivor_id"], "ASSIGNED")["status"] == "ASSIGNED"


def test_reports_outside_the_aoi_and_below_confirmation_are_refused(
    mission_state: AppState,
) -> None:
    state = mission_state
    assert state.survivors.ingest_report(
        "INS-01", _report(12.9000, 77.5000)
    ) is None
    assert state.survivors.ingest_report(
        "INS-01", _report(*GRID[0], confidence=0.61)
    ) is None
    assert state.survivors.ingest_report(
        "INS-01", _report(*GRID[0], frames_confirmed=2)
    ) is None
    assert state.survivors.list() == []
    reasons = [event["payload"]["reason"] for event in state.bus.recent(50)
               if event["topic"] == "survivor.rejected"]
    assert len(reasons) == 3
