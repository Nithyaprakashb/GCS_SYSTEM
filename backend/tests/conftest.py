"""Test fixtures.

Every test runs against a temporary data directory so the developer's real
mission database and evidence store are never touched. The environment is set
before importing any service module.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TEST_DATA = Path(tempfile.mkdtemp(prefix="rescueswarm-test-"))
os.environ["RESCUESWARM_DATA"] = str(TEST_DATA)

from rescueswarm.config import Config, SAMPLES_DIR  # noqa: E402
from rescueswarm.state import AppState  # noqa: E402


@pytest.fixture()
def sample_kml() -> bytes:
    return (SAMPLES_DIR / "sample_search_zone_10ha.kml").read_bytes()


@pytest.fixture()
def config() -> Config:
    return Config()


@pytest.fixture()
def state() -> AppState:
    app_state = AppState()
    yield app_state
    app_state.db.close()


@pytest.fixture()
def mission_state(state: AppState, sample_kml: bytes) -> AppState:
    """A state with a fresh planned mission and an initialised kit ledger."""
    state.mission.import_kml(sample_kml, "sample_search_zone_10ha.kml", operator="pytest")
    state.mission.create_plan(operator="pytest")
    return state
