"""Mission replay assembled from recorded telemetry, events and state changes."""

from __future__ import annotations

from typing import Any

from .db import Database


class ReplayEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def replay(self, mission_id: str) -> dict[str, Any]:
        mission = self.db.get_mission(mission_id)
        if not mission:
            raise KeyError(f"Unknown mission {mission_id}")
        plan = self.db.get_latest_plan(mission_id)
        survivors = self.db.get_survivors(mission_id)
        assignments = self.db.get_assignments(mission_id)
        kits = self.db.get_kits(mission_id)
        events = list(reversed(self.db.get_events(mission_id, limit=2000)))
        tracks: dict[str, list[dict[str, Any]]] = {}
        times: list[float] = []
        for callsign in ("INS-01", "DEL-01", "DEL-02"):
            rows = self.db.telemetry_track(mission_id, callsign)
            samples = [
                {
                    "t": row["receive_time"],
                    "lat": row["lat"],
                    "lon": row["lon"],
                    "alt_rel_m": row["alt_rel_m"],
                    "agl_m": row["agl_m"],
                    "speed_ms": row["speed_ms"],
                    "heading_deg": row["heading_deg"],
                    "battery_pct": row["battery_pct"],
                    "mode": row["mode"],
                    "armed": bool(row["armed"]),
                    "quality": row["quality"],
                }
                for row in rows
                if row["lat"] is not None and row["lon"] is not None
            ]
            tracks[callsign] = samples
        timeline = [
            {
                "t": event["receive_time"],
                "topic": event["topic"],
                "severity": event["severity"],
                "source": event["source"],
                "data_quality": event["data_quality"],
                "summary": self._summarise(event),
            }
            for event in events
            if not event["topic"].startswith("fleet.telemetry")
        ]
        return {
            "mission": mission,
            "plan": plan,
            "survivors": survivors,
            "assignments": assignments,
            "kits": kits,
            "tracks": tracks,
            "timeline": timeline,
            "markers": [
                {
                    "survivor_id": survivor["survivor_id"],
                    "lat": survivor["lat"],
                    "lon": survivor["lon"],
                    "drop_lat": survivor["drop_lat"],
                    "drop_lon": survivor["drop_lon"],
                    "status": survivor["status"],
                    "owner": survivor.get("owner"),
                    "kit_id": survivor.get("kit_id"),
                    "first_seen": survivor.get("first_seen"),
                    "data_quality": survivor.get("data_quality", "SIMULATED"),
                }
                for survivor in survivors
            ],
            "counts": {
                "telemetry_samples": sum(len(track) for track in tracks.values()),
                "events": len(timeline),
                "survivors": len(survivors),
            },
            "evidence": "Recorded data; simulated samples remain labelled SIMULATED.",
        }

    @staticmethod
    def _summarise(event: dict[str, Any]) -> str:
        topic = event["topic"]
        payload = event.get("payload", {})
        if topic == "flight.phase":
            return f"{payload.get('callsign')} → {payload.get('phase')}: {payload.get('detail', '')}"
        if topic == "survivor.created":
            survivor = payload.get("survivor", {})
            return f"Survivor {survivor.get('survivor_id')} confirmed by {survivor.get('source_aircraft')}"
        if topic == "survivor.updated":
            survivor = payload.get("survivor", {})
            return f"Survivor {survivor.get('survivor_id')} updated ({survivor.get('status')})"
        if topic == "allocation.created":
            assignment = payload.get("assignment", {})
            return (f"{assignment.get('survivor_id')} assigned to {assignment.get('callsign')} "
                    f"with kit {assignment.get('kit_id')}")
        if topic == "delivery.state":
            return (f"{payload.get('callsign')} {payload.get('survivor_id')} "
                    f"{payload.get('previous')} → {payload.get('state')}")
        if topic == "payload.result":
            return (f"{payload.get('kit_id')} {payload.get('result')} "
                    f"(beam {payload.get('beam_result')})")
        if topic == "safety.interlock":
            return f"Safety: {payload.get('rule_id', 'interlock')} — {payload.get('detail', '')}"
        if topic == "command.sent":
            tx = payload.get("tx", {})
            return f"{tx.get('callsign')} command {tx.get('label')}"
        if topic == "mission.phase":
            return f"Mission phase {payload.get('previous')} → {payload.get('phase')}"
        return str(payload.get("detail") or topic)
