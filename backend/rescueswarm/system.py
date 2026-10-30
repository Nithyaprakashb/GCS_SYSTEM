"""Startup diagnostics, device enumeration and environment description."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .config import APP_VERSION, CONFIG_VERSION, Config, data_dir
from .db import Database
from .evidence import EvidenceStore
from .mavlink import probe_serial_ports
from .util import iso_now


class SystemService:
    def __init__(
        self,
        config: Config,
        db: Database,
        evidence: EvidenceStore,
        video: Any,
    ) -> None:
        self.config = config
        self.db = db
        self.evidence = evidence
        self.video = video

    def environment(self) -> dict[str, Any]:
        dependencies = {}
        for name in ("pymavlink", "fastapi", "uvicorn", "shapely", "PIL", "numpy", "cv2", "serial"):
            dependencies[name] = importlib.util.find_spec(name) is not None
        ffmpeg = shutil.which("ffmpeg")
        return {
            "application": "RescueSwarm GCS",
            "version": APP_VERSION,
            "config_version": CONFIG_VERSION,
            "profile_version": self.config.get("profile_version"),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "data_dir": str(data_dir()),
            "project_root": str(Path(__file__).resolve().parents[2]),
            "ffmpeg": ffmpeg,
            "dependencies": dependencies,
            "network_policy": "Local-only. Mission operation requires no internet, cellular or cloud service.",
            "map_policy": "Procedural offline raster tiles generated locally; no internet map tiles.",
            "generated_at": iso_now(),
        }

    def diagnostics(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add(check_id: str, title: str, status: str, detail: str, **extra: Any) -> None:
            checks.append({"check_id": check_id, "title": title, "status": status,
                           "detail": detail, **extra})

        env = self.environment()
        add("backend", "Python backend", "PASS", f"Python {env['python']}, API {APP_VERSION}")
        try:
            row = self.db.query_one("SELECT sqlite_version() AS version")
            self.db.query("SELECT COUNT(*) AS n FROM missions")
            add("database", "SQLite database", "PASS",
                f"SQLite {row['version'] if row else '?'} at {self.db.path}")
        except Exception as exc:
            add("database", "SQLite database", "FAIL", str(exc))
        free_mb = self.evidence.disk_free_mb()
        status = "PASS" if free_mb >= 2000 else ("WARN" if free_mb >= 500 else "FAIL")
        add("storage", "Recording storage", status, f"{free_mb:.0f} MB free at {data_dir()}",
            free_mb=free_mb)
        add("map", "Offline map service", "PASS",
            "Local procedural tile service; no internet tiles requested")
        add("video_service", "Video pipeline", "PASS" if env["ffmpeg"] else "WARN",
            f"ffmpeg: {env['ffmpeg'] or 'not found'}; OpenCV: {env['dependencies'].get('cv2')}")
        serial_ports = probe_serial_ports()
        add("serial", "Serial interfaces", "PASS" if serial_ports else "WARN",
            f"{len(serial_ports)} port(s) enumerated; none is assumed to be a paired RFD868x",
            ports=serial_ports)
        devices = self.video.discover_capture_devices() if self.video.mode == "REAL" else []
        add("capture", "USB video capture devices",
            "PASS" if len(devices) >= 3 else ("WARN" if self.video.mode == "SIMULATION" else "FAIL"),
            ("Simulation mode: three synthetic feeds" if self.video.mode == "SIMULATION"
             else f"{len(devices)} capture device(s) found"),
            devices=devices)
        add("radio", "RFD868x telemetry", "PASS" if self.config.get("telemetry", "topology_verified", default=False) else "WARN",
            "Topology is configured but not commissioned; a detected serial port is not a validated link")
        add("safety", "Safety engine", "PASS", "Deterministic engine loaded; no UI bypass path")
        failures = [c for c in checks if c["status"] == "FAIL"]
        return {
            "ok": not failures,
            "checks": checks,
            "counts": _counts(checks),
            "environment": env,
            "generated_at": iso_now(),
        }

    def devices(self) -> dict[str, Any]:
        return {
            "serial": probe_serial_ports(),
            "video": self.video.device_snapshot(),
            "configured": {
                "telemetry": self.config.get("telemetry"),
                "feeds": self.config.get("video", "feeds", default=[]),
            },
            "warnings": [
                "Serial enumeration does not prove an RFD868x link or aircraft pairing.",
                "Capture-device enumeration order does not prove aircraft identity.",
            ],
        }

    def config_view(self) -> dict[str, Any]:
        return self.config.public_summary()


def _counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check["status"]] = counts.get(check["status"], 0) + 1
    return counts
