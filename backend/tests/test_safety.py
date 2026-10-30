"""Independent safety rules: proximity, pad and release clearance."""

from __future__ import annotations

from rescueswarm.safety import SafetyEngine
from rescueswarm.state import AppState


def _vehicle(callsign: str, *, lat: float, lon: float, armed: bool = False, agl: float = 0.0,
             stale: bool = False, battery: float = 90.0) -> dict:
    return {
        "callsign": callsign,
        "lat": lat,
        "lon": lon,
        "alt_rel_m": agl,
        "agl_m": agl,
        "armed": armed,
        "stale": stale,
        "battery_pct": battery,
        "connected": True,
    }


def test_parked_aircraft_do_not_raise_proximity(state: AppState) -> None:
    pad_lat = float(state.config.get("mission", "pad_lat"))
    pad_lon = float(state.config.get("mission", "pad_lon"))
    vehicles = {
        "INS-01": _vehicle("INS-01", lat=pad_lat, lon=pad_lon),
        "DEL-01": _vehicle("DEL-01", lat=pad_lat, lon=pad_lon),
        "DEL-02": _vehicle("DEL-02", lat=pad_lat, lon=pad_lon),
    }
    assert state.safety.separation_warnings(vehicles) == []


def test_airborne_aircraft_near_another_raises_warning(state: AppState) -> None:
    pad_lat = float(state.config.get("mission", "pad_lat"))
    pad_lon = float(state.config.get("mission", "pad_lon"))
    vehicles = {
        "INS-01": _vehicle("INS-01", lat=pad_lat, lon=pad_lon, armed=True, agl=12.0),
        "DEL-01": _vehicle("DEL-01", lat=pad_lat, lon=pad_lon, armed=True, agl=10.0),
    }
    warnings = state.safety.separation_warnings(vehicles)
    assert len(warnings) == 1
    assert warnings[0]["pair"] == "INS-01-DEL-01"
    assert warnings[0]["horizontal_m"] == 0.0


def test_release_requires_hover_verification(mission_state: AppState) -> None:
    state = mission_state
    pad_lat = float(state.config.get("mission", "pad_lat"))
    pad_lon = float(state.config.get("mission", "pad_lon"))
    vehicles = {"DEL-01": _vehicle("DEL-01", lat=pad_lat, lon=pad_lon, armed=True, agl=3.0)}
    result = state.safety.release_clearance(
        "DEL-01", "S-01", "DEL-01-K01",
        12.9494, 77.5986,
        vehicles, state.mission.aoi,
        hover_verified=False,
    )
    assert result["allowed"] is False
    hover = [check for check in result["checks"] if check["check_id"] == "hover_stable"]
    assert hover and hover[0]["ok"] is False


def test_pad_interlock_blocks_two_armed_aircraft(state: AppState) -> None:
    pad_lat = float(state.config.get("mission", "pad_lat"))
    pad_lon = float(state.config.get("mission", "pad_lon"))
    vehicles = {
        "DEL-01": _vehicle("DEL-01", lat=pad_lat, lon=pad_lon, armed=True),
        "DEL-02": _vehicle("DEL-02", lat=pad_lat, lon=pad_lon, armed=True),
    }
    pad = state.safety.pad_status(vehicles)
    assert pad["violation"] is True
    assert len(pad["occupants"]) == 2


def test_engine_has_no_llm_dependency() -> None:
    """Safety evaluation is deterministic; no network client is imported."""
    import inspect

    source = inspect.getsource(SafetyEngine)
    assert "urllib" not in source
    assert "requests" not in source
    assert "openai" not in source
