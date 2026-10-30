"""Per-mission evidence folders and small file-writing helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from .config import data_dir
from .util import iso_now, json_safe


class EvidenceStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else data_dir() / "Missions"
        self.root.mkdir(parents=True, exist_ok=True)

    def mission_dir(self, mission_id: str) -> Path:
        path = self.root / mission_id
        for child in ("plan", "telemetry", "events", "survivors", "video", "reports"):
            (path / child).mkdir(parents=True, exist_ok=True)
        return path

    def ensure_manifest(self, mission: dict[str, Any]) -> Path:
        folder = self.mission_dir(mission["mission_id"])
        manifest_path = folder / "manifest.json"
        if not manifest_path.exists():
            manifest = {
                "mission_id": mission["mission_id"],
                "name": mission["name"],
                "mode": mission["mode"],
                "created_at": mission["created_at"],
                "config_version": mission.get("config_version"),
                "application": "RescueSwarm GCS",
                "evidence_policy": "Measured, calculated, simulated and manual data are labelled separately.",
                "created_by_gcs": iso_now(),
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return folder

    def path(self, mission_id: str, *parts: str) -> Path:
        return self.mission_dir(mission_id).joinpath(*parts)

    def write_json(self, mission_id: str, relative: str, payload: Any) -> Path:
        path = self.path(mission_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_safe(payload), indent=2), encoding="utf-8")
        return path

    def write_text(self, mission_id: str, relative: str, text: str) -> Path:
        path = self.path(mission_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def append_jsonl(self, mission_id: str, relative: str, payload: Any) -> None:
        path = self.path(mission_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(json_safe(payload), separators=(",", ":")) + "\n")

    def write_csv(self, mission_id: str, relative: str, rows: Iterable[dict[str, Any]], fields: list[str]) -> Path:
        path = self.path(mission_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def archive_size_mb(self, mission_id: str) -> float:
        folder = self.mission_dir(mission_id)
        total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        return round(total / (1024 * 1024), 2)

    def disk_free_mb(self) -> float:
        return round(shutil.disk_usage(self.root).free / (1024 * 1024), 1)
