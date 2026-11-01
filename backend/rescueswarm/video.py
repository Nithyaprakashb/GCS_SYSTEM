"""Three-channel operator video: simulation source, FFmpeg capture, health, recording.

The simulated feeds are clearly watermarked synthetic optical feeds. In REAL mode
no frames are fabricated: an unmapped or absent capture device is reported as
NO_SIGNAL / UNMAPPED. Analog video is never used to identify an aircraft
automatically; identity comes from a commissioned device mapping.
"""

from __future__ import annotations

import io
import math
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import Config
from .db import Database
from .events import EventBus
from .evidence import EvidenceStore
from .telemetry import TelemetryManager
from .util import iso_now

FRAME_WIDTH = 720
FRAME_HEIGHT = 405

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None
    np = None


@dataclass
class VideoFeed:
    feed_id: str
    callsign: str
    receiver: str
    device_fingerprint: str | None
    mapped: bool = False
    status: str = "NO_SIGNAL"
    message: str = "Not started"
    fps: float = 0.0
    last_frame_mono: float = 0.0
    last_unique_mono: float = 0.0
    last_hash: int = 0
    frozen_s: float = 0.0
    luma: float | None = None
    black: bool = False
    recording: bool = False
    recording_path: str | None = None
    width: int = FRAME_WIDTH
    height: int = FRAME_HEIGHT
    source: str = "NONE"
    latency_estimate_ms: float | None = None
    frames_received: int = 0
    # Cumulative frames stay on the operator's screen; the fps estimate needs a
    # windowed counter, and reusing one field for both made the total reset every
    # health pass.
    window_frames: int = 0
    dropped: int = 0
    # Why latency is or is not a number. The GCS cannot see the far end of an analog
    # link: TX800 glass-to-glass delay needs a timing pattern and a camera on the
    # ground, which has never been done here. It is reported, never invented.
    latency_basis: str = "NOT_MEASURED"
    # When the capture thread for this feed was launched. The stall detector needs a
    # first frame to measure an age against, so a pipeline that never produced one had
    # no age at all and looked healthy forever. This gives the zero-frame case a clock.
    started_mono: float = 0.0
    # The zero-frame alarm fires once per capture session, not once per health pass.
    no_frame_event: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "feed_id": self.feed_id,
            "callsign": self.callsign,
            "receiver": self.receiver,
            "device_fingerprint": self.device_fingerprint,
            "mapped": self.mapped,
            "status": self.status,
            "message": self.message,
            "fps": round(self.fps, 1),
            "frame_age_ms": round((time.monotonic() - self.last_frame_mono) * 1000, 0)
            if self.last_frame_mono else None,
            "frozen_s": round(self.frozen_s, 1),
            "luma": round(self.luma, 1) if self.luma is not None else None,
            "black": self.black,
            "recording": self.recording,
            "recording_path": self.recording_path,
            "resolution": f"{self.width}x{self.height}",
            "source": self.source,
            "latency_estimate_ms": round(self.latency_estimate_ms, 0)
            if self.latency_estimate_ms is not None else None,
            "latency_basis": self.latency_basis,
            "frames_received": self.frames_received,
            "dropped": self.dropped,
            # Seconds a mapped feed has been waiting for its very first frame. None once
            # a frame exists — at that point frame_age_ms tells the real story.
            "no_frame_s": round(time.monotonic() - self.started_mono, 1)
            if self.started_mono and not self.frames_received else None,
            "stream_url": f"/api/video/{self.callsign}/stream",
            "still_url": f"/api/video/{self.callsign}/frame.jpg",
        }


class VideoManager:
    def __init__(
        self,
        config: Config,
        db: Database,
        bus: EventBus,
        telemetry: TelemetryManager,
        evidence: EvidenceStore,
    ) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.telemetry = telemetry
        self.evidence = evidence
        self.mode = "SIMULATION"
        self.feeds: dict[str, VideoFeed] = {}
        self.latest: dict[str, bytes] = {}
        self._lock = threading.RLock()
        # Separates "start/stop the capture machinery" from "read the latest frame". The
        # plain lock is a lifecycle token, not a data guard, and is deliberately not
        # reentrant: a start that reaches for it twice is a bug, not a nesting quirk.
        self._lifecycle_lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self._recorders: dict[str, Any] = {}
        # video_sessions row id per open recording, so closing a recorder can stamp the
        # same audit row it opened rather than leaving an orphan with no end_time.
        self._session_rows: dict[str, int] = {}
        self._recorded_frames: dict[str, int] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._health_thread: threading.Thread | None = None
        self._injected: dict[str, str | None] = {}
        self._mission_id: str | None = None
        self._fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
        self._init_feeds()

    def _init_feeds(self) -> None:
        self.feeds.clear()
        for feed_cfg in self.config.get("video", "feeds", default=[]):
            feed = VideoFeed(
                feed_id=feed_cfg["feed_id"],
                callsign=feed_cfg["callsign"],
                receiver=feed_cfg.get("receiver", f"RX-{feed_cfg['feed_id']}"),
                device_fingerprint=feed_cfg.get("device_fingerprint"),
                mapped=self.mode == "SIMULATION",
                status="NO_SIGNAL",
                message="Not started",
                source="SIMULATED" if self.mode == "SIMULATION" else "CAPTURE",
            )
            self.feeds[feed.callsign] = feed

    def start(self, mode: str) -> dict[str, Any]:
        # /video/start runs in a worker thread while /video/stop and station shutdown run on
        # the event loop. Without one lifecycle token shared between them, a capture spawned
        # just after the other call cleared the table is never terminated: the orphan keeps
        # the DirectShow device and keeps writing to the recorder.
        with self._lifecycle_lock:
            return self._start_locked(mode)

    def _start_locked(self, mode: str) -> dict[str, Any]:
        self._stop_locked()
        self.mode = mode
        self._init_feeds()
        self._stop.clear()
        if mode == "SIMULATION":
            for callsign, feed in self.feeds.items():
                feed.mapped = True
                # Not "LIVE" yet. The status becomes LIVE when the loop has actually put a
                # frame on screen, so a stalled synthetic feed cannot pass preflight.
                feed.status = "NO_SIGNAL"
                feed.message = "Synthetic feed starting; no frame yet"
                feed.started_mono = time.monotonic()
                thread = threading.Thread(target=self._sim_loop, args=(callsign,), daemon=True,
                                          name=f"video-sim-{callsign}")
                self._threads[callsign] = thread
                thread.start()
            self.bus.publish("video.health", self.health(), source="VIDEO", data_quality="SIMULATED")
        else:
            devices = self.discover_capture_devices()
            claimed: set[str] = set()
            for feed in self.feeds.values():
                match = next(
                    (d for d in devices if feed.device_fingerprint and d["name"] == feed.device_fingerprint),
                    None,
                )
                if match and match["name"] in claimed:
                    # A DirectShow source is exclusive: a second opener either fails or
                    # silently steals the feed from the aircraft already using it.
                    feed.mapped = False
                    feed.status = "UNMAPPED"
                    feed.message = "Capture device is already claimed by another feed"
                    continue
                feed.mapped = match is not None
                if match:
                    claimed.add(match["name"])
                    feed.status = "NO_SIGNAL"
                    feed.message = "Capture configured; starting"
                    feed.started_mono = time.monotonic()
                    thread = threading.Thread(
                        target=self._ffmpeg_loop, args=(feed.callsign, match["name"]), daemon=True,
                        name=f"video-{feed.callsign}",
                    )
                    self._threads[feed.callsign] = thread
                    thread.start()
                else:
                    feed.status = "UNMAPPED"
                    feed.message = "No commissioned capture-device mapping"
            self.bus.publish("video.health", self.health(), source="VIDEO", data_quality="LIVE")
        if self._health_thread is None or not self._health_thread.is_alive():
            self._health_thread = threading.Thread(target=self._health_loop, daemon=True, name="video-health")
            self._health_thread.start()
        return self.health()

    def stop(self) -> dict[str, Any]:
        """Close every capture and recorder process this manager owns.

        The shell depends on this being real: a GCS that is killed while ffmpeg still
        holds the DirectShow device leaves the camera unusable until the station is
        rebooted, and an orphaned recorder keeps appending frames after the sortie.
        """
        with self._lifecycle_lock:
            return self._stop_locked()

    def _stop_locked(self) -> dict[str, Any]:
        self._stop.set()
        closed_processes = 0
        for callsign, process in list(self._processes.items()):
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=2.0)
                closed_processes += 1
            except Exception:
                try:
                    process.kill()
                    closed_processes += 1
                except Exception:
                    pass
        self._processes.clear()
        joined = 0
        for thread in list(self._threads.values()):
            thread.join(timeout=1.5)
            if not thread.is_alive():
                joined += 1
        self._threads.clear()
        if self._health_thread is not None and self._health_thread.is_alive():
            self._health_thread.join(timeout=1.5)
        self._health_thread = None
        recorders = len(self._recorders)
        self.stop_recording()
        with self._lock:
            self.latest.clear()
        self._injected.clear()
        for feed in self.feeds.values():
            feed.status = "NO_SIGNAL"
            feed.message = "Stopped"
            feed.recording = False
            feed.recording_path = None
            feed.fps = 0.0
            feed.frames_received = 0
            feed.window_frames = 0
            feed.dropped = 0
            feed.frozen_s = 0.0
            feed.black = False
            feed.luma = None
            feed.latency_estimate_ms = None
            feed.latency_basis = "NOT_MEASURED"
            feed.last_frame_mono = 0.0
            feed.last_unique_mono = 0.0
            feed.started_mono = 0.0
            feed.no_frame_event = False
        return {
            "capture_processes": closed_processes,
            "recorders_closed": recorders,
            "threads_joined": joined,
            "threads_expected": len(self.feeds),
        }

    # ------------------------------------------------------------ simulation
    def _sim_loop(self, callsign: str) -> None:
        fps = float(self.config.get("video", "sim_fps", default=6))
        interval = 1.0 / max(1.0, fps)
        last_emit = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_emit < interval:
                time.sleep(0.01)
                continue
            last_emit = now
            feed = self.feeds[callsign]
            fault = self._injected.get(callsign)
            if fault == "loss":
                feed.status = "NO_SIGNAL"
                feed.message = "Injected signal loss"
                continue
            state = self.telemetry.vehicle(callsign)
            image = self._render_sim_frame(feed, state, now)
            if fault == "black":
                image = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (0, 0, 0))
                feed.black = True
                feed.status = "BLACK"
                feed.message = "Black frame detected"
            else:
                feed.black = False
            if fault == "freeze" and callsign in self.latest:
                frame_bytes = self.latest[callsign]
                feed.frozen_s = (now - feed.last_unique_mono)
                # Frames are still arriving, they are just the same frame: say LIVE until
                # the freeze has actually lasted long enough to be a fault, then say so.
                if feed.frozen_s > 2.0:
                    feed.status = "FROZEN"
                    feed.message = "Injected frozen frame"
                else:
                    feed.status = "RECORDING" if feed.recording else "LIVE"
                    feed.message = "Synthetic optical feed"
            else:
                frame_bytes = self._encode_jpeg(image, feed)
                feed.last_unique_mono = now
                feed.status = "RECORDING" if feed.recording else "LIVE"
                feed.message = "Synthetic optical feed"
            with self._lock:
                self.latest[callsign] = frame_bytes
            feed.last_frame_mono = now
            feed.fps = fps
            feed.luma = self._estimate_luma(image)
            # A synthetic feed has no radio in it. Reporting a latency here would put a
            # fabricated link number on the operator's screen, so the value is None and
            # the reason is stated.
            feed.latency_estimate_ms = None
            feed.latency_basis = "SYNTHETIC_FEED_NO_LINK"
            # The frame counters are written last: a reader that sees a non-zero count
            # is guaranteed to see the honest metrics for that frame too.
            feed.frames_received += 1
            feed.window_frames += 1
            self._record_frame(callsign, image, jpeg=frame_bytes)

    def _font(self, size: int, mono: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = (size, mono)
        if key in self._fonts:
            return self._fonts[key]
        candidates = (
            ["C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/consolab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
            if mono else
            ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/seguisb.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        )
        font: ImageFont.ImageFont = ImageFont.load_default()
        for path in candidates:
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, size)
                    break
                except OSError:
                    continue
        self._fonts[key] = font  # type: ignore[assignment]
        return font

    def _render_sim_frame(self, feed: VideoFeed, state: dict[str, Any], now: float) -> Image.Image:
        image = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (14, 20, 18))
        draw = ImageDraw.Draw(image, "RGBA")
        lat = state.get("lat") or self.config.get("mission", "pad_lat")
        lon = state.get("lon") or self.config.get("mission", "pad_lon")
        pad_lat = float(self.config.get("mission", "pad_lat"))
        pad_lon = float(self.config.get("mission", "pad_lon"))
        # Approximate local metres from the pad; adequate for a synthetic optical scene.
        world_x = (lon - pad_lon) * 111_320.0 * math.cos(math.radians(pad_lat))
        world_y = (lat - pad_lat) * 110_540.0
        alt = float(state.get("agl_m") or state.get("alt_rel_m") or 12.0)
        scale = max(4.0, 140.0 / max(alt, 2.0))
        heading = math.radians(float(state.get("heading_deg") or 0.0))
        cos_h, sin_h = math.cos(heading), math.sin(heading)

        # Ground fields: deterministic blocks on a 22 m world grid.
        cell = 22.0
        radius_m = (FRAME_WIDTH / scale) * 0.9
        start_x = math.floor((world_x - radius_m) / cell)
        end_x = math.ceil((world_x + radius_m) / cell)
        start_y = math.floor((world_y - radius_m) / cell)
        end_y = math.ceil((world_y + radius_m) / cell)
        for gx in range(start_x, end_x + 1):
            for gy in range(start_y, end_y + 1):
                seed = (gx * 73856093) ^ (gy * 19349663) ^ 0x9E3779B9
                tone = (seed & 0xFF)
                base = 34 + tone % 30
                color = (base, base + 12 + (tone % 18), base - 4, 255)
                # Rotate the field rectangle around the camera position.
                corners = []
                for dx, dy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
                    wx = (gx + dx) * cell - world_x
                    wy = (gy + dy) * cell - world_y
                    rx = wx * cos_h + wy * sin_h
                    ry = -wx * sin_h + wy * cos_h
                    corners.append((FRAME_WIDTH / 2 + rx * scale, FRAME_HEIGHT / 2 + ry * scale))
                draw.polygon(corners, fill=color)
                if tone % 7 == 0:
                    draw.line(corners[0] + corners[1], fill=(26, 34, 27, 255), width=2)

        # A dirt track and a few tree clusters, anchored in world coordinates.
        track_offset = (math.floor(world_y / 90.0) * 90.0) - world_y
        track_screen_y = FRAME_HEIGHT / 2 + track_offset * scale
        draw.rectangle([0, track_screen_y - 5 * scale, FRAME_WIDTH, track_screen_y + 5 * scale],
                       fill=(62, 54, 42, 255))
        for index in range(-4, 5):
            wx = index * 34.0
            wy = 18.0 * math.sin(index * 1.3)
            rx = (wx - world_x) * cos_h + (wy - world_y) * sin_h
            ry = -(wx - world_x) * sin_h + (wy - world_y) * cos_h
            sx = FRAME_WIDTH / 2 + rx * scale
            sy = FRAME_HEIGHT / 2 + ry * scale
            if -40 < sx < FRAME_WIDTH + 40 and -40 < sy < FRAME_HEIGHT + 40:
                r = max(5, int(2.2 * scale))
                draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(20, 46, 28, 210))

        # Subtle sensor texture / scanlines.
        for y in range(0, FRAME_HEIGHT, 3):
            draw.line([(0, y), (FRAME_WIDTH, y)], fill=(255, 255, 255, 6))
        draw.rectangle([0, 0, FRAME_WIDTH, FRAME_HEIGHT], outline=(210, 245, 255, 40), width=2)

        # Reticle and corner brackets.
        cx, cy = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
        draw.line([(cx - 16, cy), (cx - 5, cy)], fill=(235, 250, 255, 220), width=1)
        draw.line([(cx + 5, cy), (cx + 16, cy)], fill=(235, 250, 255, 220), width=1)
        draw.line([(cx, cy - 16), (cx, cy - 5)], fill=(235, 250, 255, 220), width=1)
        draw.line([(cx, cy + 5), (cx, cy + 16)], fill=(235, 250, 255, 220), width=1)
        for x, y, dx, dy in ((18, 18, 1, 1), (FRAME_WIDTH - 18, 18, -1, 1),
                             (18, FRAME_HEIGHT - 18, 1, -1), (FRAME_WIDTH - 18, FRAME_HEIGHT - 18, -1, -1)):
            draw.line([(x, y), (x + 16 * dx, y)], fill=(240, 250, 255, 230), width=2)
            draw.line([(x, y), (x, y + 16 * dy)], fill=(240, 250, 255, 230), width=2)

        # HUD.
        mono = self._font(13, mono=True)
        small = self._font(11, mono=True)
        top = f"{feed.callsign}  •  {feed.receiver}  •  RUNCAM NANO 2 / TX800"
        draw.rectangle([18, 18, 360, 38], fill=(4, 10, 14, 150))
        draw.text((24, 22), top, font=mono, fill=(225, 245, 250, 240))
        mode = state.get("mode") or "SIM"
        alt_text = f"{alt:04.1f} m AGL"
        spd = float(state.get("groundspeed_ms") or 0.0)
        batt = state.get("battery_pct")
        bottom = f"{mode:<8}  {alt_text:<12}  {spd:4.1f} m/s  BAT {batt if batt is not None else '--':>3}%"
        draw.rectangle([18, FRAME_HEIGHT - 42, 500, FRAME_HEIGHT - 18], fill=(4, 10, 14, 160))
        draw.text((24, FRAME_HEIGHT - 38), bottom, font=small, fill=(220, 240, 245, 235))
        stamp = iso_now().replace("T", " ")[:23] + "Z"
        draw.rectangle([FRAME_WIDTH - 235, FRAME_HEIGHT - 42, FRAME_WIDTH - 18, FRAME_HEIGHT - 18],
                       fill=(4, 10, 14, 160))
        draw.text((FRAME_WIDTH - 228, FRAME_HEIGHT - 38), stamp, font=small, fill=(220, 240, 245, 235))
        if feed.recording:
            draw.ellipse([FRAME_WIDTH - 110, 26, FRAME_WIDTH - 100, 36], fill=(235, 60, 60, 255))
            draw.text((FRAME_WIDTH - 92, 24), "REC", font=mono, fill=(240, 120, 120, 240))
        draw.rectangle([18, FRAME_HEIGHT - 66, 402, FRAME_HEIGHT - 48], fill=(120, 30, 30, 165))
        draw.text((24, FRAME_HEIGHT - 64), "SIMULATION — SYNTHETIC OPTICAL FEED — NOT FLIGHT FOOTAGE",
                  font=small, fill=(255, 220, 210, 245))
        return image

    @staticmethod
    def _encode_jpeg(image: Image.Image, feed: VideoFeed) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=False)
        return buffer.getvalue()

    @staticmethod
    def _estimate_luma(image: Image.Image) -> float:
        small = image.convert("L").resize((32, 18))
        data = small.tobytes()  # one byte per pixel in mode "L"
        return float(sum(data) / len(data))

    # ------------------------------------------------------------- real mode
    def discover_capture_devices(self) -> list[dict[str, Any]]:
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, timeout=8,
            )
            text = (result.stderr or "") + (result.stdout or "")
            devices = []
            for line in text.splitlines():
                match = re.search(r'"([^"]+)"\s+\(video\)', line)
                if match:
                    devices.append({"name": match.group(1), "type": "video", "fingerprint": match.group(1)})
            return devices
        except Exception:
            return []

    def _ffmpeg_loop(self, callsign: str, device_name: str) -> None:
        feed = self.feeds[callsign]
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "dshow", "-rtbufsize", "100M", "-i", f"video={device_name}",
            "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}",
            "-r", "10", "-f", "mjpeg", "-q:v", "5", "-",
        ]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       bufsize=0)
            self._processes[callsign] = process
            assert process.stdout is not None
            buffer = b""
            frame_count = 0
            while not self._stop.is_set():
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    start = buffer.find(b"\xff\xd8")
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if start < 0 or end < 0:
                        buffer = buffer[-2048:]
                        break
                    jpeg = buffer[start:end + 2]
                    buffer = buffer[end + 2:]
                    now = time.monotonic()
                    feed.last_frame_mono = now
                    feed.frames_received += 1
                    feed.window_frames += 1
                    frame_count += 1
                    unique = hash(jpeg) != feed.last_hash
                    if unique:
                        feed.last_hash = hash(jpeg)
                        feed.last_unique_mono = now
                        with self._lock:
                            self.latest[callsign] = jpeg
                        try:
                            image = Image.open(io.BytesIO(jpeg))
                            feed.luma = self._estimate_luma(image)
                            feed.black = (feed.luma or 0) < float(
                                self.config.get("video", "black_luma_threshold", default=12)
                            )
                            self._record_frame(callsign, image, jpeg=jpeg)
                        except Exception:
                            # A frame that will not decode is a lost frame, not a silent one.
                            feed.dropped += 1
                    feed.status = "BLACK" if feed.black else ("RECORDING" if feed.recording else "LIVE")
                    feed.message = "Capture active" if not feed.black else "Black frame"
            process.wait(timeout=2)
            feed.status = "NO_SIGNAL"
            feed.message = "Capture process exited"
        except Exception as exc:
            feed.status = "CAPTURE_ERROR"
            feed.message = str(exc)
        finally:
            self._processes.pop(callsign, None)

    # --------------------------------------------------------------- health
    def _health_loop(self) -> None:
        freeze_timeout = float(self.config.get("video", "freeze_timeout_s", default=2.0))
        # How long a pipeline that is supposed to be running may stay silent before the
        # operator is told, in seconds, that nothing has ever arrived. The stall detector
        # below needs a first frame to age against, so before this branch existed a
        # capture process that produced zero frames sat at NO_SIGNAL "starting" forever
        # and looked indistinguishable from one that had simply not been polled yet.
        no_frame_timeout = float(self.config.get("video", "no_frame_timeout_s", default=8.0))
        last = time.monotonic()
        while not self._stop.is_set():
            time.sleep(0.5)
            now = time.monotonic()
            delta = now - last
            last = now
            changed = False
            for feed in self.feeds.values():
                if feed.last_frame_mono:
                    age = now - feed.last_frame_mono
                    if age > freeze_timeout and feed.status in {"LIVE", "RECORDING", "FROZEN"}:
                        feed.frozen_s = age
                        if feed.status != "FROZEN":
                            feed.status = "FROZEN"
                            feed.message = "No new frames"
                            changed = True
                    elif feed.status == "FROZEN" and age <= freeze_timeout:
                        feed.status = "RECORDING" if feed.recording else "LIVE"
                        feed.frozen_s = 0.0
                        feed.message = "Feed recovered"
                        changed = True
                    if feed.window_frames:
                        measured = feed.window_frames / max(delta, 0.001)
                        feed.fps = measured if feed.fps == 0 else 0.7 * feed.fps + 0.3 * measured
                        feed.window_frames = 0
                elif feed.started_mono:
                    wait = now - feed.started_mono
                    if wait >= no_frame_timeout:
                        detail = f"No frame {round(wait)} s after capture start"
                        if feed.message != detail:
                            # Each pass rewrites only the number; the status is already the
                            # honest NO_SIGNAL, which is what preflight refuses to clear.
                            feed.message = detail
                            changed = True
                        if not feed.no_frame_event:
                            feed.no_frame_event = True
                            changed = True
                            self.bus.publish(
                                "video.no_frame",
                                {"callsign": feed.callsign, "feed_id": feed.feed_id,
                                 "receiver": feed.receiver, "mode": self.mode,
                                 "wait_s": round(wait, 1),
                                 "detail": "Capture pipeline is running but has never "
                                           "delivered a frame; the receiver may be "
                                           "unlocked or the device mapping is wrong"},
                                severity="warning", source="VIDEO",
                                data_quality="SIMULATED" if self.mode == "SIMULATION" else "LIVE",
                            )
                if feed.recording and self._mission_id and not feed.recording_path:
                    feed.recording_path = str(self.evidence.path(self._mission_id, "video", f"{feed.callsign}.mp4"))
            if changed:
                self.bus.publish("video.health", self.health(), source="VIDEO",
                                 data_quality="SIMULATED" if self.mode == "SIMULATION" else "LIVE")

    def health(self) -> dict[str, Any]:
        feeds = [feed.to_dict() for feed in self.feeds.values()]
        live = sum(1 for feed in feeds if feed["status"] in {"LIVE", "RECORDING"})
        return {
            "mode": self.mode,
            "feeds": feeds,
            "live_count": live,
            "total": len(feeds),
            # Started, silent, never-produced-a-frame. Reported separately so the console
            # can say "2 live, 1 dead on arrival" instead of a single misleading count.
            "waiting_count": sum(1 for feed in feeds if feed["no_frame_s"] is not None),
            "recording_count": sum(1 for feed in feeds if feed["recording"]),
            "policy": "Analog video is an operator aid, not a flight-control channel. Mapping is commissioned, not guessed.",
        }

    def device_snapshot(self) -> dict[str, Any]:
        """Enumerated capture hardware and the commissioned mapping.

        Enumeration order is never accepted as aircraft identity.
        """
        devices = self.discover_capture_devices() if self.mode == "REAL" else []
        return {
            "mode": self.mode,
            "devices": devices,
            "feeds": [
                {
                    "callsign": feed.callsign,
                    "receiver": feed.receiver,
                    "fingerprint": feed.device_fingerprint,
                    "mapped": feed.mapped,
                }
                for feed in self.feeds.values()
            ],
            "note": "Device enumeration order is not identity; each feed requires visual commissioning.",
        }

    # ------------------------------------------------------------- recording
    def start_recording(self, mission_id: str) -> dict[str, Any]:
        """Open one recorder per mapped feed, fed by the pipeline that is already running.

        A DirectShow source can only be opened once per process pair: opening a second
        `video=<device>` ffmpeg for recording steals the device from the live capture and
        the operator loses the feed mid-sortie. REAL therefore encodes the MJPEG frames the
        capture loop already produced instead of touching the device again.

        A feed that has never delivered a frame is refused: an empty .mp4 in the evidence
        folder reads as "video was captured" to whoever assembles the sortie report.
        """
        self._mission_id = mission_id
        results = {}
        for callsign, feed in self.feeds.items():
            blocker = self._recording_blocker(callsign, feed)
            if blocker:
                results[callsign] = {"recording": False, "error": blocker}
                continue
            path = self.evidence.path(mission_id, "video", f"{callsign}.mp4")
            path.parent.mkdir(parents=True, exist_ok=True)
            if self.mode == "SIMULATION":
                if cv2 is None:
                    results[callsign] = {"recording": False, "error": "OpenCV unavailable"}
                    continue
                writer = cv2.VideoWriter(
                    str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                    float(self.config.get("video", "sim_fps", default=6)),
                    (FRAME_WIDTH, FRAME_HEIGHT),
                )
                if not writer.isOpened():
                    writer.release()
                    results[callsign] = {"recording": False, "error": "VideoWriter unavailable"}
                    continue
                self._recorders[callsign] = writer
                self._finalise_recording_start(callsign, feed, mission_id, path,
                                               {"recording": True, "path": str(path)}, results)
            elif self.mode == "REAL":
                recorder = self._open_pipe_recorder(path)
                if isinstance(recorder, str):
                    results[callsign] = {"recording": False, "error": recorder}
                    continue
                self._recorders[callsign] = recorder
                self._finalise_recording_start(
                    callsign, feed, mission_id, path,
                    {"recording": True, "path": str(path), "pending_test": True,
                     "recorded_from": "CAPTURE_STREAM",
                     "note": "H.264 encode of the MJPEG frames the capture loop delivered; "
                             "the analog link itself is not recorded."},
                    results,
                )
            else:
                results[callsign] = {"recording": False, "error": f"Unknown mode {self.mode}"}
        self.bus.publish("video.recording", results, source="VIDEO",
                         data_quality="SIMULATED" if self.mode == "SIMULATION" else "LIVE")
        return results

    def _recording_blocker(self, callsign: str, feed: VideoFeed) -> str | None:
        """Why this feed cannot be recorded, in the order the operator needs to hear it.

        The mapping and pipeline answers come first in REAL because they say what to fix;
        the frame count is last because a running-but-silent pipeline is the case the
        recorder used to accept and turn into an empty evidence file.
        """
        if self.mode == "REAL":
            if not (feed.mapped and feed.device_fingerprint):
                return "Feed not mapped"
            thread = self._threads.get(callsign)
            if thread is None or not thread.is_alive():
                return "Capture pipeline is not running; nothing to record"
        if feed.frames_received == 0:
            return "Feed has never delivered a frame; there is nothing to record"
        return None

    def _open_pipe_recorder(self, path: Path) -> Any:
        """ffmpeg stdin recorder; returns the process or an error string."""
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(self.REAL_RECORD_FPS),
            "-i", "pipe:0", "-an", "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", str(path),
        ]
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            return f"Recorder unavailable: {exc}"
        return process

    def _finalise_recording_start(self, callsign: str, feed: VideoFeed, mission_id: str,
                                 path: Path, result: dict[str, Any],
                                 results: dict[str, Any]) -> None:
        feed.recording = True
        feed.recording_path = str(path)
        feed.status = "RECORDING"
        results[callsign] = result
        self._recorded_frames[callsign] = 0
        self._session_rows[callsign] = self.db.record_video_session({
            "mission_id": mission_id, "feed_id": feed.feed_id, "callsign": callsign,
            "receiver": feed.receiver, "fingerprint": feed.device_fingerprint,
            "recording_path": str(path), "mode": self.mode,
            "start_time": iso_now(), "health": feed.to_dict(),
        })

    REAL_RECORD_FPS = 10.0  # matches the capture pipeline's -r 10

    def _record_frame(self, callsign: str, image: Image.Image, jpeg: bytes | None = None) -> None:
        """Hand one accepted frame to the open recorder, whichever kind it is."""
        writer = self._recorders.get(callsign)
        if writer is None:
            return
        stdin = getattr(writer, "stdin", None)
        if stdin is not None:  # REAL: the already-decoded MJPEG frame
            payload = jpeg
            if payload is None:
                payload = self._encode_jpeg(image, self.feeds[callsign])
            try:
                stdin.write(payload)
                self._recorded_frames[callsign] = self._recorded_frames.get(callsign, 0) + 1
            except (BrokenPipeError, ValueError, OSError):
                self._recorders.pop(callsign, None)
            return
        if cv2 is None or np is None:
            return
        try:
            frame = np.array(image.convert("RGB"))[:, :, ::-1]
            writer.write(frame)
            self._recorded_frames[callsign] = self._recorded_frames.get(callsign, 0) + 1
        except Exception:
            pass

    def stop_recording(self) -> None:
        # A recorder whose pipe broke was dropped from _recorders by _record_frame, but its
        # database row is still open. Iterating the union is what lets that row be closed
        # with complete=False instead of being left as a start with no end.
        for callsign in sorted(set(self._recorders) | set(self._session_rows)):
            writer = self._recorders.get(callsign)
            closed = True
            if writer is None:
                closed = False
            else:
                try:
                    stdin = getattr(writer, "stdin", None)
                    if stdin is not None:
                        try:
                            stdin.close()
                        except Exception:
                            pass
                        try:
                            code = writer.wait(timeout=5)
                            closed = code == 0
                        except subprocess.TimeoutExpired:
                            writer.kill()
                            closed = False
                    elif hasattr(writer, "release"):
                        writer.release()
                    elif hasattr(writer, "terminate"):
                        writer.terminate()
                except Exception:
                    closed = False
            feed = self.feeds.get(callsign)
            session_row = self._session_rows.pop(callsign, 0)
            frames_written = self._recorded_frames.pop(callsign, 0)
            if session_row:
                self.db.close_video_session(
                    session_row, end_time=iso_now(), frames_recorded=frames_written,
                    complete=closed and frames_written > 0,
                    health={"frames_written": frames_written, "recorder_closed": closed,
                            "mode": self.mode,
                            "feed": feed.to_dict() if feed else None},
                )
            if feed:
                feed.recording = False
                if feed.status == "RECORDING":
                    feed.status = "LIVE"
                if not closed and feed.recording_path:
                    self.bus.publish(
                        "video.recording_incomplete",
                        {"callsign": callsign, "path": feed.recording_path,
                         "detail": "Recorder exited non-zero; the file may be truncated"},
                        severity="warning", source="VIDEO",
                        data_quality="SIMULATED" if self.mode == "SIMULATION" else "LIVE",
                    )
        self._recorders.clear()

    # --------------------------------------------------------------- access
    def latest_frame(self, callsign: str) -> bytes | None:
        with self._lock:
            return self.latest.get(callsign)

    def inject(self, callsign: str, fault: str | None) -> dict[str, Any]:
        if callsign not in self.feeds:
            raise ValueError(f"Unknown feed {callsign}")
        allowed = {None, "loss", "freeze", "black"}
        if fault not in allowed:
            raise ValueError(f"Unsupported video fault {fault}")
        self._injected[callsign] = fault
        self.bus.publish("video.injected", {"callsign": callsign, "fault": fault},
                         severity="warning", source="SIMULATION", data_quality="SIMULATED")
        return {"callsign": callsign, "fault": fault}

    def mjpeg_stream(self, callsign: str) -> Generator[bytes, None, None]:
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n"
        last = b""
        while not self._stop.is_set():
            frame = self.latest_frame(callsign)
            if frame and frame is not last:
                last = frame
                yield boundary + frame + b"\r\n"
            time.sleep(0.08)
