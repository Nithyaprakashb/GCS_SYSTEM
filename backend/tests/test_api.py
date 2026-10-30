"""HTTP surface: loopback guard, security headers, state and reset semantics."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rescueswarm.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_offline(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["offline"] is True
    assert body["mode"] in {"SIMULATION", "REAL"}


def test_host_header_is_restricted(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 403


def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_map_style_has_no_glyph_server(client: TestClient) -> None:
    style = client.get("/api/map/style").json()
    assert "glyphs" not in style
    sources = style["sources"]
    assert sources
    source = next(iter(sources.values()))
    assert source["type"] == "raster"
    assert source["tiles"][0].startswith("/api/map/tiles/")


def test_state_contains_required_sections(client: TestClient) -> None:
    state = client.get("/api/state").json()
    for key in ("app", "mission", "fleet", "safety", "survivors", "kits", "allocations",
                "deliveries", "video", "preflight", "events"):
        assert key in state
    assert state["app"]["mode"] == "SIMULATION"
    assert state["app"]["offline"] is True


def test_reset_clears_session(client: TestClient) -> None:
    response = client.post("/api/mission/reset")
    assert response.status_code == 200
    body = response.json()
    assert body["phase"] == "SETUP"
    assert body["mission_id"] is None


def test_copilot_is_read_only_and_answers_goal_prompts(client: TestClient) -> None:
    for question in ("Mission risk summary", "Why this allocation?", "What blocks deliveries?"):
        response = client.post("/api/copilot/ask", json={"question": question})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"]
        assert body["engine"] == "rule-based"
        assert "cannot arm, fly" in body["safety_note"].lower() or "advisory" in body["safety_note"].lower()


def test_no_endpoint_accepts_raw_mavlink(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/mission/start" in paths
    assert not any("mavlink" in path or "servo" in path for path in paths)
