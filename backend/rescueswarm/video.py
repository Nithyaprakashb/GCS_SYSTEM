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
    dropped: int = 0

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
            "frames_received": self.frames_received,
            "dropped": self.dropped,
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
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self._recorders: dict[str, Any] = {}
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
        self.stop()
        self.mode = mode
        self._init_feeds()
        self._stop.clear()
        if mode == "SIMULATION":
            for callsign, feed in self.feeds.items():
                feed.mapped = True
                feed.status = "LIVE"
                feed.message = "Synthetic optical feed"
                thread = threading.Thread(target=self._sim_loop, args=(callsign,), daemon=True,
                                          name=f"video-sim-{callsign}")
                self._threads[callsign] = thread
                thread.start()
            self.bus.publish("video.health", self.health(), source="VIDEO", data_quality="SIMULATED")
        else:
            devices = self.discover_capture_devices()
            for feed in self.feeds.values():
                match = next(
                    (d for d in devices if feed.device_fingerprint and d["name"] == feed.device_fingerprint),
                    None,
                )
                feed.mapped = match is not None
                if match:
                    feed.status = "NO_SIGNAL"
                    feed.message = "Capture configured; starting"
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

    def stop(self) -> None:
        self._stop.set()
        for feed_id, process in list(self._processes.items()):
            try:
                process.terminate()
            except Exception:
                pass
        self._processes.clear()
        for thread in list(self._threads.values()):
            thread.join(timeout=1.0)
        self._threads.clear()
        self.stop_recording()
        with self._lock:
            self.latest.clear()
        for feed in self.feeds.values():
            feed.status = "NO_SIGNAL"
            feed.recording = False

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
                feed.frames_received += 0
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
                if feed.frozen_s > 2.0:
                    feed.status = "FROZEN"
                    feed.message = "Injected frozen frame"
            else:
                frame_bytes = self._encode_jpeg(image, feed)
                feed.last_unique_mono = now
                feed.status = "RECORDING" if feed.recording else "LIVE"
                feed.message = "Synthetic optical feed"
            with self._lock:
                self.latest[callsign] = frame_bytes
            feed.last_frame_mono = now
            feed.frames_received += 1
            feed.fps = fps
            feed.luma = self._estimate_luma(image)
            feed.latency_estimate_ms = 35.0
            self._record_frame(callsign, image)

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
        data = list(small.getdata())
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
                            self._record_frame(callsign, image)
                        except Exception:
                            pass
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
                    if feed.frames_received:
                        measured = feed.frames_received / max(delta, 0.001)
                        feed.fps = measured if feed.fps == 0 else 0.7 * feed.fps + 0.3 * measured
                        feed.frames_received = 0
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
        self._mission_id = mission_id
        results = {}
        for callsign, feed in self.feeds.items():
            path = self.evidence.path(mission_id, "video", f"{callsign}.mp4")
            path.parent.mkdir(parents=True, exist_ok=True)
            if self.mode == "SIMULATION" and cv2 is not None:
                writer = cv2.VideoWriter(
                    str(path), cv2.VideoWriter_fourcc(*"mp4v"), 6.0, (FRAME_WIDTH, FRAME_HEIGHT)
                )
                if writer.isOpened():
                    self._recorders[callsign] = writer
                    feed.recording = True
                    feed.recording_path = str(path)
                    results[callsign] = {"recording": True, "path": str(path)}
                    self.db.record_video_session(
                        {"mission_id": mission_id, "feed_id": feed.feed_id, "callsign": callsign,
                         "receiver": feed.receiver, "fingerprint": feed.device_fingerprint,
                         "recording_path": str(path), "start_time": iso_now(),
                         "health": feed.to_dict()}
                    )
                else:
                    results[callsign] = {"recording": False, "error": "VideoWriter unavailable"}
            elif self.mode == "REAL" and feed.mapped and feed.device_fingerprint:
                command = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "dshow", "-rtbufsize", "100M", "-i", f"video={feed.device_fingerprint}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                    "-pix_fmt", "yuv420p", str(path),
                ]
                try:
                    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._recorders[callsign] = process
                    feed.recording = True
                    feed.recording_path = str(path)
                    results[callsign] = {"recording": True, "path": str(path), "pending_test": True}
                except Exception as exc:
                    results[callsign] = {"recording": False, "error": str(exc)}
            else:
                results[callsign] = {"recording": False, "error": "Feed not mapped"}
        self.bus.publish("video.recording", results, source="VIDEO",
                         data_quality="SIMULATED" if self.mode == "SIMULATION" else "LIVE")
        return results

    def _record_frame(self, callsign: str, image: Image.Image) -> None:
        writer = self._recorders.get(callsign)
        if writer is None or cv2 is None or np is None:
            return
        try:
            frame = np.array(image.convert("RGB"))[:, :, ::-1]
            writer.write(frame)
        except Exception:
            pass

    def stop_recording(self) -> None:
        for callsign, writer in list(self._recorders.items()):
            try:
                if hasattr(writer, "release"):
                    writer.release()
                elif hasattr(writer, "terminate"):
                    writer.terminate()
            except Exception:
                pass
            feed = self.feeds.get(callsign)
            if feed:
                feed.recording = False
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
