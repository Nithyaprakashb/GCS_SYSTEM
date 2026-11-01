"""Capture and recorder lifecycle: one device, one opener; nothing invented.

REAL capture needs a physical TX800 receiver stick, which this repository has
never been connected to. These tests therefore drive the manager against a fake
ffmpeg so the *decisions* are executable — device contention, where recording
gets its frames, honest latency reporting, teardown — while the analog path
itself stays PENDING TEST.
"""

from __future__ import annotations

import io
import threading
import time
from typing import Any

import pytest

from rescueswarm import video as video_module
from rescueswarm.state import AppState

DEVICE = "TX800-USB-Capture"


class BlockingStdout:
    """stdout that yields nothing until the process is terminated."""

    def __init__(self, process: "FakeProcess") -> None:
        self.process = process

    def read(self, _size: int) -> bytes:
        self.process.ended.wait(5.0)
        return b""


class FakeProcess:
    def __init__(self) -> None:
        self.ended = threading.Event()
        self.stdin = io.BytesIO()
        self.stdout = BlockingStdout(self)
        self.returncode: int | None = None
        self.killed = False

    def terminate(self) -> None:
        self.ended.set()
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.ended.set()
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.ended.wait(timeout or 5.0)
        return self.returncode if self.returncode is not None else 0


class SpawnLog:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, command: list[str], **_kwargs: Any) -> FakeProcess:
        self.commands.append(list(command))
        process = FakeProcess()
        self.processes.append(process)
        return process


@pytest.fixture()
def spawns(monkeypatch) -> SpawnLog:
    log = SpawnLog()
    monkeypatch.setattr(video_module.subprocess, "Popen", log)
    return log


def _real_manager(state: AppState, monkeypatch, fingerprints: dict[str, str | None],
                  devices: list[dict[str, str]] | None = None) -> video_module.VideoManager:
    """Put the manager in REAL mode with a commissioned device mapping, no hardware."""
    feeds = [dict(feed) for feed in state.config.data["video"]["feeds"]]
    for feed in feeds:
        feed["device_fingerprint"] = fingerprints.get(feed["callsign"])
    state.config.data["video"]["feeds"] = feeds
    manager = state.video
    monkeypatch.setattr(manager, "discover_capture_devices",
                        lambda: list(devices if devices is not None else [{"name": DEVICE, "type": "video",
                                                                          "fingerprint": DEVICE}]))
    manager.start("REAL")
    return manager


def _deliver_a_frame(manager: video_module.VideoManager, callsign: str) -> None:
    """Pretend the capture loop produced one frame.

    The fake ffmpeg in these tests writes nothing to its stdout, so the frame counter
    has to be advanced by hand. Recording is gated on that counter, and the gate is the
    point: an empty file is not evidence of a recording.
    """
    feed = manager.feeds[callsign]
    feed.frames_received += 1
    feed.last_frame_mono = time.monotonic()


def _wait_for_event(bus, topic: str, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if any(event["topic"] == topic for event in bus.recent(300)):
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------------- contention
def test_a_capture_device_is_only_ever_claimed_once(state: AppState, monkeypatch,
                                                   spawns: SpawnLog) -> None:
    manager = _real_manager(
        state, monkeypatch,
        {"INS-01": DEVICE, "DEL-01": DEVICE, "DEL-02": None},
    )
    try:
        by_call = {feed.callsign: feed for feed in manager.feeds.values()}
        assert by_call["INS-01"].mapped is True
        # DirectShow is exclusive: a second opener would steal or break the first feed.
        assert by_call["DEL-01"].mapped is False
        assert "already claimed" in by_call["DEL-01"].message
        assert by_call["DEL-02"].status == "UNMAPPED"
        assert len(spawns.commands) == 1, "only the first feed may open the device"
    finally:
        manager.stop()


def test_unmapped_feeds_never_start_a_capture_process(state: AppState, monkeypatch,
                                                      spawns: SpawnLog) -> None:
    manager = _real_manager(state, monkeypatch, {"INS-01": None, "DEL-01": None, "DEL-02": None})
    try:
        assert spawns.commands == []
        assert all(feed.status == "UNMAPPED" for feed in manager.feeds.values())
        assert all(feed.mapped is False for feed in manager.feeds.values())
    finally:
        manager.stop()


# -------------------------------------------------------------------- recording
def test_real_recording_encodes_the_capture_stream_not_a_second_device_open(
    state: AppState, monkeypatch, spawns: SpawnLog
) -> None:
    manager = _real_manager(state, monkeypatch, {"INS-01": DEVICE})
    try:
        capture_command = spawns.commands[0]
        assert "dshow" in capture_command
        spawns.commands.clear()
        # Recording is gated on the feed having actually delivered a frame; the fake
        # capture writes no stdout, so advance the counter the way the loop would.
        _deliver_a_frame(manager, "INS-01")

        results = manager.start_recording("M-VIDEO-TEST")
        assert results["INS-01"]["recording"] is True
        assert results["INS-01"]["pending_test"] is True
        assert results["INS-01"]["recorded_from"] == "CAPTURE_STREAM"
        recorder_command = spawns.commands[0]
        joined = " ".join(recorder_command)
        # The whole point: the recorder reads the frames the capture loop already has.
        assert "image2pipe" in recorder_command
        assert "pipe:0" in joined
        assert "dshow" not in recorder_command
        assert f"video={DEVICE}" not in joined

        feed = manager.feeds["INS-01"]
        assert feed.recording is True
        assert feed.status == "RECORDING"

        manager.stop_recording()
        assert feed.recording is False
        assert feed.status != "RECORDING"
        assert manager._recorders == {}
    finally:
        manager.stop()


def test_real_recording_refuses_a_feed_with_no_running_capture(
    state: AppState, monkeypatch, spawns: SpawnLog
) -> None:
    manager = _real_manager(state, monkeypatch, {"INS-01": DEVICE})
    manager.stop()
    results = manager.start_recording("M-VIDEO-TEST")
    assert results["INS-01"]["recording"] is False
    assert "not running" in results["INS-01"]["error"]
    # The only ffmpeg invocation so far is the capture; no recorder was spawned.
    assert all("image2pipe" not in command for command in spawns.commands)


def test_real_recording_refuses_an_unmapped_feed(state: AppState, monkeypatch,
                                                 spawns: SpawnLog) -> None:
    manager = _real_manager(state, monkeypatch, {"INS-01": DEVICE, "DEL-01": None})
    try:
        results = manager.start_recording("M-VIDEO-TEST")
        assert results["DEL-01"] == {"recording": False, "error": "Feed not mapped"}
    finally:
        manager.stop()


# ---------------------------------------------------------------------- honesty
def test_simulated_feeds_report_no_latency_number(state: AppState) -> None:
    manager = state.video
    manager.start("SIMULATION")
    try:
        # Every feed must have produced a frame before the honesty assertions run.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if all(feed.frames_received > 0 for feed in manager.feeds.values()):
                break
            time.sleep(0.05)
        feeds = manager.health()["feeds"]
        assert all(feed["frames_received"] > 0 for feed in feeds)
        for feed in feeds:
            # A synthetic feed has no radio: 35 ms here would be a fabricated link metric.
            assert feed["latency_estimate_ms"] is None
            assert feed["latency_basis"] == "SYNTHETIC_FEED_NO_LINK"
            assert feed["dropped"] == 0
    finally:
        manager.stop()


def test_stop_clears_injected_faults_and_running_state(state: AppState) -> None:
    manager = state.video
    manager.start("SIMULATION")
    manager.inject("INS-01", "loss")
    assert manager._injected["INS-01"] == "loss"
    summary = manager.stop()
    assert manager._injected == {}
    assert summary["threads_expected"] == len(manager.feeds)
    for feed in manager.feeds.values():
        assert feed.status == "NO_SIGNAL"
        assert feed.recording is False
        assert feed.fps == 0.0
        assert feed.last_frame_mono == 0.0


def test_switching_modes_rebuilds_the_feed_table(state: AppState, monkeypatch,
                                                 spawns: SpawnLog) -> None:
    manager = state.video
    manager.start("SIMULATION")
    assert all(feed.source == "SIMULATED" for feed in manager.feeds.values())
    _real_manager(state, monkeypatch, {"INS-01": DEVICE})
    try:
        assert manager.mode == "REAL"
        assert manager.feeds["INS-01"].source == "CAPTURE"
        assert manager.feeds["INS-01"].latency_basis == "NOT_MEASURED"
        # The simulated table must not survive into REAL: no synthetic feed may be
        # mistaken for a receiver that is actually carrying aircraft video.
        assert manager.feeds["DEL-02"].status == "UNMAPPED"
    finally:
        manager.stop()


# --------------------------------------------------------------- zero frames
def test_recording_refuses_a_feed_that_never_delivered_a_frame(
    state: AppState, monkeypatch, spawns: SpawnLog
) -> None:
    manager = _real_manager(state, monkeypatch, {"INS-01": DEVICE})
    try:
        feed = manager.feeds["INS-01"]
        assert feed.frames_received == 0, "the fake capture writes no stdout"
        results = manager.start_recording("M-VIDEO-TEST")
        # The capture thread being alive used to be enough, and the result was an empty
        # .mp4 filed as evidence that video had been captured.
        assert results["INS-01"]["recording"] is False
        assert "never delivered a frame" in results["INS-01"]["error"]
        assert feed.recording is False
        assert all("image2pipe" not in command for command in spawns.commands)
    finally:
        manager.stop()


def test_a_started_but_silent_feed_is_never_reported_live(state: AppState, monkeypatch,
                                                          spawns: SpawnLog) -> None:
    """A pipeline that has produced nothing is waiting, not healthy.

    The stall detector needs a first frame to age against, so a zero-frame feed used to
    sit at NO_SIGNAL with no number attached and preflight had nothing to show the
    operator about why the feed was not up.
    """
    state.config.data["video"]["no_frame_timeout_s"] = 0.2
    manager = _real_manager(state, monkeypatch, {"INS-01": DEVICE})
    try:
        ins = next(f for f in manager.health()["feeds"] if f["callsign"] == "INS-01")
        assert ins["status"] != "LIVE"
        assert ins["frames_received"] == 0
        assert ins["no_frame_s"] is not None
        assert manager.health()["waiting_count"] == 1
        assert _wait_for_event(state.bus, "video.no_frame"), \
            "the health loop must announce a feed that never produced a frame"
    finally:
        manager.stop()


def test_a_feed_that_produced_frames_has_no_waiting_clock(state: AppState) -> None:
    manager = state.video
    manager.start("SIMULATION")
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if all(feed.frames_received > 0 for feed in manager.feeds.values()):
                break
            time.sleep(0.05)
        feeds = manager.health()["feeds"]
        assert all(feed["no_frame_s"] is None for feed in feeds)
        assert manager.health()["waiting_count"] == 0
    finally:
        manager.stop()


def test_a_closed_recording_stamps_its_session_row(
    state: AppState, monkeypatch, spawns: SpawnLog
) -> None:
    """The evidence table has to be able to say a recording finished.

    Rows were inserted at start and never touched again, so a post-flight review could
    not tell a complete file from a session whose recorder died mid-sortie.
    """
    from PIL import Image

    manager = _real_manager(state, monkeypatch, {"INS-01": DEVICE})
    try:
        _deliver_a_frame(manager, "INS-01")
        assert manager.start_recording("M-SESSION-TEST")["INS-01"]["recording"] is True
        image = Image.new("RGB", (video_module.FRAME_WIDTH, video_module.FRAME_HEIGHT))
        manager._record_frame("INS-01", image, jpeg=b"\xff\xd8frame\xff\xd9")
        manager.stop_recording()

        row = state.db.query_one(
            "SELECT * FROM video_sessions WHERE mission_id = ? AND callsign = ?",
            ("M-SESSION-TEST", "INS-01"),
        )
        assert row is not None
        assert row["mode"] == "REAL"
        assert row["end_time"], "stop_recording must close the session it opened"
        assert row["frames_recorded"] == 1
        assert row["complete"] == 1
    finally:
        manager.stop()


def test_a_recorder_that_died_mid_flight_is_closed_as_incomplete(
    state: AppState, monkeypatch, spawns: SpawnLog
) -> None:
    from PIL import Image

    manager = _real_manager(state, monkeypatch, {"INS-01": DEVICE})
    try:
        _deliver_a_frame(manager, "INS-01")
        manager.start_recording("M-BROKEN-TEST")
        recorder = manager._recorders["INS-01"]
        # A closed pipe is what _record_frame sees when ffmpeg exits early: it drops the
        # recorder and leaves only the open database row behind.
        recorder.stdin.close()
        manager._record_frame("INS-01", Image.new("RGB", (16, 16)), jpeg=b"\xff\xd8x\xff\xd9")
        assert "INS-01" not in manager._recorders
        manager.stop_recording()
        row = state.db.query_one("SELECT * FROM video_sessions WHERE mission_id = ?",
                                 ("M-BROKEN-TEST",))
        assert row is not None
        assert row["end_time"] is not None
        assert row["complete"] == 0, "a session whose recorder broke is never called complete"
    finally:
        manager.stop()
