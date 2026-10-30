"""Mission analytics and offline report generation.

Reports are written into the mission evidence folder as JSON, Markdown, CSV and
a dependency-free PDF. Every simulated mission is labelled as simulated; the
PDF is a mission record, not a claim of flight validation.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .db import Database
from .evidence import EvidenceStore
from .util import iso_now

PAGE_W = 595
PAGE_H = 842


class _Pdf:
    """Minimal, dependency-free PDF text writer (A4, Helvetica)."""

    def __init__(self) -> None:
        self.pages: list[list[str]] = []
        self.current: list[str] = []
        self.y = PAGE_H - 64

    def line(self, text: str, size: float = 10.5, leading: float = 15.0) -> None:
        if self.y < 56:
            self.new_page()
        safe = (
            text.encode("latin-1", "replace").decode("latin-1")
            .replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        )
        self.current.append(f"BT /F1 {size:.1f} Tf 56 {self.y:.1f} Td ({safe}) Tj ET")
        self.y -= leading

    def gap(self, height: float = 8.0) -> None:
        self.y -= height

    def rule(self) -> None:
        if self.y < 56:
            self.new_page()
        self.current.append(f"0.75 w 56 {self.y + 6:.1f} m 539 {self.y + 6:.1f} l S")
        self.y -= 6

    def new_page(self) -> None:
        if self.current:
            self.pages.append(self.current)
        self.current = []
        self.y = PAGE_H - 64

    def save(self, path: Path) -> None:
        self.new_page()
        objects: list[bytes] = []

        def add(content: bytes) -> int:
            objects.append(content)
            return len(objects)

        font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        page_ids: list[int] = []
        content_ids: list[int] = []
        for page in self.pages:
            stream = "\n".join(page).encode("latin-1", "replace")
            content_ids.append(add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"))
        pages_id = len(objects) + len(self.pages) + 1
        for _ in self.pages:
            page_ids.append(add(
                b"<< /Type /Page /Parent " + str(pages_id).encode() + b" 0 R /MediaBox [0 0 "
                + f"{PAGE_W} {PAGE_H}".encode() + b"] /Resources << /Font << /F1 "
                + str(font_id).encode() + b" 0 R >> >> /Contents " + str(content_ids[len(page_ids)]).encode() + b" 0 R >>"
            ))
        pages_obj = add(
            b"<< /Type /Pages /Kids [" + b" ".join(str(i).encode() + b" 0 R" for i in page_ids)
            + b"] /Count " + str(len(page_ids)).encode() + b" >>"
        )
        # The Pages object id must match the placeholder used by each page.
        # Objects were appended in order, so pages_id equals pages_obj only when
        # the placeholder computation above is exact; recompute defensively.
        catalog_id = add(b"<< /Type /Catalog /Pages " + str(pages_obj).encode() + b" 0 R >>")
        output = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(output))
            output += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
        xref_pos = len(output)
        output += f"xref\n0 {len(objects) + 1}\n".encode()
        output += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            output += f"{offset:010d} 00000 n \n".encode()
        output += (
            b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root "
            + str(catalog_id).encode() + b" 0 R >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
        )
        path.write_bytes(bytes(output))


class Reporter:
    def __init__(self, db: Database, evidence: EvidenceStore) -> None:
        self.db = db
        self.evidence = evidence

    # ---------------------------------------------------------------- reports
    def generate(
        self,
        mission_id: str,
        summary: dict[str, Any],
        plan: dict[str, Any] | None,
        aoi: dict[str, Any] | None,
    ) -> dict[str, Any]:
        mission = self.db.get_mission(mission_id) or {}
        events = self.db.get_events(mission_id, limit=5000)
        telemetry = self._telemetry_stats(mission_id)
        analytics = self.analytics(mission_id, summary=summary, events=events,
                                   telemetry=telemetry)
        report = {
            "generated_at": iso_now(),
            "mission": mission,
            "summary": summary,
            "aoi": {
                "name": (aoi or {}).get("name"),
                "area_ha": (aoi or {}).get("area_ha"),
                "perimeter_m": (aoi or {}).get("perimeter_m"),
                "source_hash": (aoi or {}).get("source_hash"),
                "valid": (aoi or {}).get("valid"),
                "warnings": (aoi or {}).get("warnings", []),
            },
            "plan": {
                "plan_id": (plan or {}).get("plan_id"),
                "plan_hash": (plan or {}).get("plan_hash"),
                "lane_count": (plan or {}).get("search", {}).get("lane_count"),
                "path_length_m": (plan or {}).get("search", {}).get("path_length_m"),
                "estimate": (plan or {}).get("estimate"),
                "geofences": (plan or {}).get("geofences"),
            },
            "telemetry": telemetry,
            "analytics": analytics,
            "events": events,
            "evidence_statement": self._evidence_statement(summary),
        }
        self.evidence.write_json(mission_id, "reports/report.json", report)
        self.evidence.write_text(mission_id, "reports/report.md", self._markdown(report))
        self._csvs(mission_id, summary, telemetry)
        try:
            self._pdf(mission_id, report)
        except Exception:
            pass
        return report

    def analytics(
        self,
        mission_id: str,
        *,
        summary: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        telemetry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary = summary or self._summary_from_db(mission_id)
        events = events if events is not None else self.db.get_events(mission_id, limit=5000)
        telemetry = telemetry or self._telemetry_stats(mission_id)
        survivors = summary.get("survivors", {}).get("survivors", [])
        kits = summary.get("kits", {}).get("kits", [])
        started = summary.get("started_at") or (self.db.get_mission(mission_id) or {}).get("started_at")
        ended = summary.get("ended_at") or (self.db.get_mission(mission_id) or {}).get("ended_at")
        duration = None
        if started and ended:
            from datetime import datetime

            try:
                duration = round((datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds(), 1)
            except ValueError:
                duration = None
        topic_counts: dict[str, int] = {}
        for event in events:
            topic_counts[event["topic"]] = topic_counts.get(event["topic"], 0) + 1
        delivered = [k for k in kits if k.get("final_status") == "DELIVERED"]
        unknown = [k for k in kits if k.get("final_status") == "UNKNOWN"]
        status_counts: dict[str, int] = {}
        for survivor in survivors:
            status_counts[survivor["status"]] = status_counts.get(survivor["status"], 0) + 1
        return {
            "duration_s": duration,
            "duration_min": round(duration / 60.0, 2) if duration else None,
            "survivors_registered": len(survivors),
            "survivor_status": status_counts,
            "kits_delivered": len(delivered),
            "kits_unknown": len(unknown),
            "kits_remaining": sum(1 for k in kits if k.get("load_status") == "LOADED"),
            "event_topics": topic_counts,
            "telemetry": {callsign: stats.get("samples") for callsign, stats in telemetry.items()},
            "allocations": len(summary.get("allocations", {}).get("assignments", [])),
            "evidence": "CALCULATED from recorded events and telemetry",
        }

    def mission_replay(self, mission_id: str) -> dict[str, Any]:
        from .replay import ReplayEngine

        return ReplayEngine(self.db).replay(mission_id)

    # ----------------------------------------------------------------- helpers
    def _summary_from_db(self, mission_id: str) -> dict[str, Any]:
        survivors = self.db.get_survivors(mission_id)
        kits = self.db.get_kits(mission_id)
        assignments = self.db.get_assignments(mission_id)
        mission = self.db.get_mission(mission_id) or {}
        return {
            "mission_id": mission_id,
            "mode": mission.get("mode"),
            "outcome": mission.get("outcome"),
            "started_at": mission.get("started_at"),
            "ended_at": mission.get("ended_at"),
            "survivors": {"survivors": survivors},
            "kits": {"kits": kits},
            "allocations": {"assignments": assignments},
        }

    def _telemetry_stats(self, mission_id: str) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for callsign in ("INS-01", "DEL-01", "DEL-02"):
            rows = self.db.query(
                """SELECT COUNT(*) AS samples, MIN(battery_pct) AS min_battery,
                          MAX(alt_rel_m) AS max_alt, MAX(receive_time) AS last_sample
                   FROM telemetry_samples WHERE mission_id = ? AND callsign = ?""",
                (mission_id, callsign),
            )
            stats[callsign] = dict(rows[0]) if rows else {}
        return stats

    @staticmethod
    def _evidence_statement(summary: dict[str, Any]) -> str:
        if summary.get("mode") == "SIMULATION":
            return (
                "SIMULATED MISSION. All telemetry, detections, deliveries and timings were produced by "
                "the offline engineering simulator. They are not flight results and must not be cited "
                "as measured performance."
            )
        return (
            "LIVE MISSION RECORD. Only values acknowledged by the aircraft and recorded by the GCS are "
            "included. Unacknowledged commands and missing break-beam evidence remain UNKNOWN."
        )

    def _markdown(self, report: dict[str, Any]) -> str:
        mission = report["mission"]
        summary = report["summary"]
        analytics = report["analytics"]
        lines = [
            f"# RescueSwarm Mission Report — {mission.get('name') or mission.get('mission_id')}",
            "",
            f"- Mission ID: `{mission.get('mission_id')}`",
            f"- Mode: **{mission.get('mode')}**",
            f"- Outcome: **{mission.get('outcome')}**",
            f"- Started: {mission.get('started_at') or '—'}",
            f"- Ended: {mission.get('ended_at') or '—'}",
            f"- Planned boundary: {(report.get('aoi') or {}).get('area_ha')} ha",
            f"- Search lanes: {(report.get('plan') or {}).get('lane_count')}",
            f"- Duration: {analytics.get('duration_min')} min",
            "",
            "## Evidence statement",
            "",
            report["evidence_statement"],
            "",
            "## Survivors",
            "",
            "| ID | Status | Owner | Kit | Latitude | Longitude | Observations |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for survivor in summary.get("survivors", {}).get("survivors", []):
            lines.append(
                f"| {survivor['survivor_id']} | {survivor['status']} | {survivor.get('owner') or '—'} | "
                f"{survivor.get('kit_id') or '—'} | {survivor['lat']:.6f} | {survivor['lon']:.6f} | "
                f"{survivor.get('observations', 1)} |"
            )
        lines += ["", "## Kit ledger", ""]
        for status, count in sorted(snapshot_counts(summary.get("kits", {})).items()):
            lines.append(f"- {status}: {count}")
        lines += ["", "## Telemetry coverage", "", "| Aircraft | Samples | Min battery | Max altitude |", "| --- | --- | --- | --- |"]
        for callsign, stats in report.get("telemetry", {}).items():
            lines.append(
                f"| {callsign} | {stats.get('samples', 0)} | {stats.get('min_battery', '—')} | {stats.get('max_alt', '—')} |"
            )
        lines += [
            "",
            "## Safety and audit",
            "",
            f"- Release policy: {summary.get('delivery', {}).get('policy', 'No automatic retry after UNKNOWN.')}",
            f"- Event records: {sum(analytics.get('event_topics', {}).values())}",
            "",
            "Generated offline by RescueSwarm GCS.",
        ]
        return "\n".join(lines) + "\n"

    def _csvs(self, mission_id: str, summary: dict[str, Any], telemetry: dict[str, Any]) -> None:
        survivors = summary.get("survivors", {}).get("survivors", [])
        if survivors:
            fields = ["survivor_id", "status", "owner", "kit_id", "lat", "lon", "drop_lat", "drop_lon",
                      "confidence", "uncertainty_m", "observations", "source_aircraft", "first_seen", "last_seen"]
            self.evidence.write_csv(
                mission_id, "survivors/survivors.csv",
                [{field: survivor.get(field) for field in fields} for survivor in survivors],
                fields,
            )
        kits = summary.get("kits", {}).get("kits", [])
        if kits:
            fields = ["kit_id", "callsign", "load_status", "reserved_for", "release_command_id",
                      "final_status", "updated_at"]
            self.evidence.write_csv(
                mission_id, "reports/kits.csv",
                [{field: kit.get(field) for field in fields} for kit in kits],
                fields,
            )
        rows = [
            {"callsign": callsign, "samples": stats.get("samples"),
             "min_battery": stats.get("min_battery"), "max_alt": stats.get("max_alt"),
             "last_sample": stats.get("last_sample")}
            for callsign, stats in telemetry.items()
        ]
        self.evidence.write_csv(
            mission_id, "reports/telemetry_summary.csv", rows,
            ["callsign", "samples", "min_battery", "max_alt", "last_sample"],
        )

    def _pdf(self, mission_id: str, report: dict[str, Any]) -> None:
        mission = report["mission"]
        summary = report["summary"]
        analytics = report["analytics"]
        pdf = _Pdf()
        pdf.line("RESCUESWARM GCS — MISSION REPORT", 16, 22)
        pdf.line(f"Mission: {mission.get('name') or mission.get('mission_id')}", 12, 18)
        pdf.line(f"Mission ID: {mission.get('mission_id')}    Mode: {mission.get('mode')}    Outcome: {mission.get('outcome')}")
        pdf.line(f"Started: {mission.get('started_at')}    Ended: {mission.get('ended_at')}")
        pdf.line(f"Duration: {analytics.get('duration_min')} min    Boundary: {(report.get('aoi') or {}).get('area_ha')} ha")
        pdf.rule()
        pdf.line("Evidence statement", 12, 18)
        for chunk in wrap(report["evidence_statement"], 96):
            pdf.line(chunk)
        pdf.gap()
        pdf.line("Survivors", 12, 18)
        pdf.line("ID      Status        Owner      Kit            Latitude    Longitude", 9.5, 13)
        for survivor in summary.get("survivors", {}).get("survivors", []):
            pdf.line(
                f"{survivor['survivor_id']:<7} {survivor['status']:<13} {(survivor.get('owner') or '-'):<10} "
                f"{(survivor.get('kit_id') or '-'):<14} {survivor['lat']:.6f}  {survivor['lon']:.6f}",
                9.5, 13,
            )
        pdf.gap()
        pdf.line("Kit ledger", 12, 18)
        for status, count in sorted(snapshot_counts(summary.get("kits", {})).items()):
            pdf.line(f"{status:<14} {count}", 10)
        pdf.gap()
        pdf.line("Telemetry coverage", 12, 18)
        for callsign, stats in report.get("telemetry", {}).items():
            pdf.line(f"{callsign}: {stats.get('samples', 0)} samples, min battery {stats.get('min_battery')}%, "
                     f"max altitude {stats.get('max_alt')} m", 10)
        pdf.gap()
        pdf.line("Policy", 12, 18)
        for chunk in wrap(str(summary.get("delivery", {}).get("policy", "")), 96):
            pdf.line(chunk)
        pdf.gap(18)
        pdf.line("Operator sign-off: ______________________     Safety pilot sign-off: ______________________", 10)
        pdf.line("Generated offline. Simulated missions are not flight evidence.", 8.5)
        pdf.save(self.evidence.path(mission_id, "reports/report.pdf"))


def snapshot_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    return snapshot.get("counts", {})


def wrap(text: str, width: int) -> list[str]:
    words = str(text).split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]
