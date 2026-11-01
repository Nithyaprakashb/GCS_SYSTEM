"""Station shutdown contract: the process must be able to stop itself cleanly.

On Windows a parent cannot send SIGTERM to a child — Electron's ``kill()`` is
TerminateProcess, which skips every handler and leaves ffmpeg holding the DirectShow
device and the event queue unwritten. So the shell asks over loopback and the backend
tears down its own children. These tests pin that contract; they do not let the real
``os._exit`` run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rescueswarm import api
from rescueswarm.main import app

BODY = {"confirmation": "SHUTDOWN", "reason": "pytest station teardown"}


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def exits(monkeypatch) -> list[float]:
    """Capture the exit request instead of ending the test session."""
    requested: list[float] = []
    monkeypatch.setattr(api, "_schedule_exit",
                        lambda delay_s=0.5: requested.append(delay_s), raising=True)
    return requested


def test_shutdown_requires_the_confirmation_token(client: TestClient, exits: list[float]) -> None:
    response = client.post("/api/system/shutdown", json={"reason": "no token"},
                           headers={"X-Operator": "nithya"})
    assert response.status_code == 400
    assert "confirmation" in response.json()["detail"].lower()
    assert client.get("/api/health").status_code == 200, "a refused shutdown must not tear anything down"
    assert exits == []


def test_shutdown_requires_an_operator_header(client: TestClient, exits: list[float]) -> None:
    response = client.post("/api/system/shutdown", json=BODY)
    assert response.status_code == 400
    assert "X-Operator" in response.json()["detail"]
    assert exits == []


def test_shutdown_is_refused_while_a_sortie_is_executing(client: TestClient,
                                                          exits: list[float]) -> None:
    state = app.state.app_state
    previous = state.mission.phase
    state.mission.phase = "EXECUTING"
    try:
        response = client.post("/api/system/shutdown", json=BODY, headers={"X-Operator": "nithya"})
        assert response.status_code == 409
        assert "sortie is executing" in response.json()["detail"]
        assert exits == [], "the process must not be asked to exit while aircraft are airborne"
        assert state._shutdown_done is False
        assert client.get("/api/health").status_code == 200
    finally:
        state.mission.phase = previous


def test_shutdown_reports_what_it_closed_without_touching_the_aircraft(
    client: TestClient, exits: list[float]
) -> None:
    state = app.state.app_state
    response = client.post("/api/system/shutdown", json=BODY,
                           headers={"X-Operator": "nithya"})
    assert response.status_code == 200
    body = response.json()
    assert body["shutting_down"] is True
    stopped = body["services_stopped"]
    for key in ("capture_processes_closed", "recorders_closed", "capture_threads_joined",
                "event_queue_drained", "events_written", "event_write_errors",
                "onboard_parameters_touched"):
        assert key in stopped, f"the teardown summary must account for {key}"
    # The GCS has no authority to disarm an onboard failsafe by closing itself.
    assert stopped["onboard_parameters_touched"] == 0
    assert stopped["event_write_errors"] == 0, "queued evidence must drain before the writer stops"
    assert state._shutdown_done is True
    assert exits == [0.5], "exactly one deferred process exit, after the response"

    # A second request must not double-close the database or spawn a second exit.
    again = client.post("/api/system/shutdown", json=BODY, headers={"X-Operator": "nithya"})
    assert again.status_code == 200
    assert again.json()["services_stopped"] == {"already_stopped": True}
    assert exits == [0.5]
