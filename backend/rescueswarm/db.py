"""SQLite persistence for mission evidence, fleet samples and the kit ledger.

The database is the durable source of truth for everything except raw video.
Writes are serialised with a lock because telemetry, video health, the mission
engine and API handlers share one local database connection.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .config import data_dir
from .util import iso_now, json_safe

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS missions (
  mission_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  mode TEXT NOT NULL,
  phase TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  config_version TEXT,
  plan_hash TEXT,
  outcome TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS telemetry_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT NOT NULL,
  callsign TEXT NOT NULL,
  source_time TEXT NOT NULL,
  receive_time TEXT NOT NULL,
  lat REAL, lon REAL, alt_rel_m REAL, agl_m REAL,
  speed_ms REAL, heading_deg REAL, battery_pct REAL,
  mode TEXT, armed INTEGER, quality TEXT,
  FOREIGN KEY (mission_id) REFERENCES missions(mission_id)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_mission_time ON telemetry_samples(mission_id, receive_time);
CREATE INDEX IF NOT EXISTS idx_telemetry_vehicle ON telemetry_samples(mission_id, callsign, receive_time);

CREATE TABLE IF NOT EXISTS connection_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT, endpoint TEXT, callsign TEXT, state TEXT,
  event_time TEXT NOT NULL, diagnostics_json TEXT
);

CREATE TABLE IF NOT EXISTS preflight_checks (
  mission_id TEXT NOT NULL,
  check_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  result TEXT NOT NULL,
  measured TEXT, threshold TEXT, evidence_source TEXT,
  timestamp TEXT NOT NULL, note TEXT,
  PRIMARY KEY (mission_id, check_id)
);

CREATE TABLE IF NOT EXISTS operator_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT, action_id TEXT, action_type TEXT NOT NULL,
  actor TEXT, target TEXT, timestamp TEXT NOT NULL,
  result TEXT, detail_json TEXT
);

CREATE TABLE IF NOT EXISTS mission_plans (
  plan_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  kml_hash TEXT, plan_hash TEXT,
  plan_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  upload_state TEXT
);

CREATE TABLE IF NOT EXISTS survivors (
  survivor_id TEXT NOT NULL,
  mission_id TEXT NOT NULL,
  first_seen TEXT, last_seen TEXT,
  lat REAL, lon REAL, uncertainty_m REAL, confidence REAL,
  status TEXT, source_aircraft TEXT,
  drop_lat REAL, drop_lon REAL,
  observations INTEGER DEFAULT 1,
  updated_at TEXT,
  PRIMARY KEY (mission_id, survivor_id)
);
CREATE INDEX IF NOT EXISTS idx_survivors_mission ON survivors(mission_id, status);

CREATE TABLE IF NOT EXISTS survivor_observations (
  observation_id TEXT PRIMARY KEY,
  mission_id TEXT,
  survivor_id TEXT NOT NULL,
  aircraft TEXT, source_time TEXT,
  frame_count INTEGER, confidence REAL,
  model_version TEXT, calibration_version TEXT,
  lat REAL, lon REAL, raw_json TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
  assignment_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  survivor_id TEXT NOT NULL,
  callsign TEXT NOT NULL,
  eta_s REAL,
  decision_json TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS kits (
  kit_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  callsign TEXT NOT NULL,
  load_status TEXT NOT NULL,
  reserved_for TEXT,
  release_command_id TEXT,
  final_status TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS payload_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT, event_id TEXT, kit_id TEXT, survivor_id TEXT,
  callsign TEXT, command TEXT, beam_result TEXT, result TEXT,
  timestamp TEXT, raw_json TEXT
);

CREATE TABLE IF NOT EXISTS safety_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT, event_id TEXT, severity TEXT, rule_id TEXT,
  callsign TEXT, measured_state TEXT, action TEXT, timestamp TEXT, detail TEXT
);

CREATE TABLE IF NOT EXISTS video_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT, feed_id TEXT, callsign TEXT, receiver TEXT,
  fingerprint TEXT, recording_path TEXT, start_time TEXT, end_time TEXT,
  health_json TEXT
);

CREATE TABLE IF NOT EXISTS mission_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT,
  mission_id TEXT,
  topic TEXT NOT NULL,
  severity TEXT,
  source TEXT,
  source_time TEXT,
  receive_time TEXT NOT NULL,
  payload_json TEXT,
  data_quality TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_mission_time ON mission_events(mission_id, receive_time);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else data_dir() / "rescueswarm.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Bring pre-2.0 databases up to the mission-scoped survivor schema."""
        columns = self._conn.execute("PRAGMA table_info(survivors)").fetchall()
        primary_key = [row["name"] for row in columns if row["pk"]]
        if primary_key == ["survivor_id"]:
            self._conn.executescript(
                """
                ALTER TABLE survivors RENAME TO survivors_legacy;
                CREATE TABLE survivors (
                  survivor_id TEXT NOT NULL,
                  mission_id TEXT NOT NULL,
                  first_seen TEXT, last_seen TEXT,
                  lat REAL, lon REAL, uncertainty_m REAL, confidence REAL,
                  status TEXT, source_aircraft TEXT,
                  drop_lat REAL, drop_lon REAL,
                  observations INTEGER DEFAULT 1,
                  updated_at TEXT,
                  PRIMARY KEY (mission_id, survivor_id)
                );
                INSERT INTO survivors
                  (survivor_id, mission_id, first_seen, last_seen, lat, lon, uncertainty_m,
                   confidence, status, source_aircraft, drop_lat, drop_lon, observations, updated_at)
                SELECT survivor_id, mission_id, first_seen, last_seen, lat, lon, uncertainty_m,
                       confidence, status, source_aircraft, drop_lat, drop_lon, observations, updated_at
                FROM survivors_legacy;
                DROP TABLE survivors_legacy;
                CREATE INDEX IF NOT EXISTS idx_survivors_mission ON survivors(mission_id, status);
                """
            )
        observation_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(survivor_observations)").fetchall()
        }
        if "mission_id" not in observation_columns:
            self._conn.execute("ALTER TABLE survivor_observations ADD COLUMN mission_id TEXT")

    # ------------------------------------------------------------------ basics
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- missions
    def create_mission(self, mission: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO missions
               (mission_id, name, mode, phase, state, created_at, config_version, notes)
               VALUES (:mission_id, :name, :mode, :phase, :state, :created_at, :config_version, :notes)""",
            mission,
        )

    def update_mission(self, mission_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        self.execute(
            f"UPDATE missions SET {columns} WHERE mission_id = ?",
            tuple(fields.values()) + (mission_id,),
        )

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM missions WHERE mission_id = ?", (mission_id,))

    def list_missions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM missions ORDER BY created_at DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ events
    def insert_event(self, event: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO mission_events
               (event_id, mission_id, topic, severity, source, source_time, receive_time, payload_json, data_quality)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.get("event_id"),
                event.get("mission_id"),
                event["topic"],
                event.get("severity", "info"),
                event.get("source"),
                event.get("source_time"),
                event.get("receive_time") or iso_now(),
                json.dumps(json_safe(event.get("payload", {})), separators=(",", ":")),
                event.get("data_quality", "UNKNOWN"),
            ),
        )

    def get_events(self, mission_id: str, limit: int = 300, topic: str | None = None) -> list[dict[str, Any]]:
        if topic:
            rows = self.query(
                "SELECT * FROM mission_events WHERE mission_id = ? AND topic = ? ORDER BY id DESC LIMIT ?",
                (mission_id, topic, limit),
            )
        else:
            rows = self.query(
                "SELECT * FROM mission_events WHERE mission_id = ? ORDER BY id DESC LIMIT ?",
                (mission_id, limit),
            )
        for row in rows:
            try:
                row["payload"] = json.loads(row.pop("payload_json") or "{}")
            except json.JSONDecodeError:
                row["payload"] = {}
        return rows

    def count_events(self, mission_id: str) -> int:
        row = self.query_one("SELECT COUNT(*) AS n FROM mission_events WHERE mission_id = ?", (mission_id,))
        return int(row["n"]) if row else 0

    # ---------------------------------------------------------------- planning
    def save_plan(self, mission_id: str, plan: dict[str, Any]) -> None:
        self.execute(
            """INSERT OR REPLACE INTO mission_plans
               (plan_id, mission_id, kml_hash, plan_hash, plan_json, created_at, upload_state)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                plan["plan_id"],
                mission_id,
                plan.get("kml_hash") or plan.get("aoi", {}).get("source_hash"),
                plan.get("plan_hash"),
                json.dumps(json_safe(plan), separators=(",", ":")),
                plan.get("created_at") or iso_now(),
                json.dumps({"status": "NOT_UPLOADED"}),
            ),
        )

    def get_latest_plan(self, mission_id: str) -> dict[str, Any] | None:
        row = self.query_one(
            "SELECT plan_json, upload_state FROM mission_plans WHERE mission_id = ? ORDER BY created_at DESC LIMIT 1",
            (mission_id,),
        )
        if not row:
            return None
        plan = json.loads(row["plan_json"])
        try:
            plan["upload"] = json.loads(row["upload_state"] or "{}")
        except json.JSONDecodeError:
            plan["upload"] = {}
        return plan

    def set_plan_upload_state(self, plan_id: str, state: dict[str, Any]) -> None:
        self.execute(
            "UPDATE mission_plans SET upload_state = ? WHERE plan_id = ?",
            (json.dumps(json_safe(state)), plan_id),
        )

    # --------------------------------------------------------------- telemetry
    def record_telemetry(self, mission_id: str, state: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO telemetry_samples
               (mission_id, callsign, source_time, receive_time, lat, lon, alt_rel_m, agl_m,
                speed_ms, heading_deg, battery_pct, mode, armed, quality)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mission_id,
                state["callsign"],
                state.get("source_time") or iso_now(),
                iso_now(),
                state.get("lat"),
                state.get("lon"),
                state.get("alt_rel_m"),
                state.get("agl_m"),
                state.get("groundspeed_ms"),
                state.get("heading_deg"),
                state.get("battery_pct"),
                state.get("mode"),
                1 if state.get("armed") else 0,
                state.get("data_quality", "UNKNOWN"),
            ),
        )

    def telemetry_track(self, mission_id: str, callsign: str, from_time: str | None = None) -> list[dict[str, Any]]:
        columns = ("receive_time, lat, lon, alt_rel_m, agl_m, speed_ms, heading_deg, "
                   "battery_pct, mode, armed, quality")
        if from_time:
            return self.query(
                f"""SELECT {columns}
                    FROM telemetry_samples
                    WHERE mission_id = ? AND callsign = ? AND receive_time >= ?
                    ORDER BY id""",
                (mission_id, callsign, from_time),
            )
        return self.query(
            f"""SELECT {columns}
                FROM telemetry_samples WHERE mission_id = ? AND callsign = ? ORDER BY id""",
            (mission_id, callsign),
        )

    # ---------------------------------------------------------------- operators
    def record_operator_action(
        self,
        mission_id: str | None,
        action_type: str,
        actor: str,
        target: str | None,
        result: str,
        detail: Any = None,
    ) -> str:
        action_id = f"ACT-{iso_now()}-{action_type}"[:80]
        self.execute(
            """INSERT INTO operator_actions
               (mission_id, action_id, action_type, actor, target, timestamp, result, detail_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mission_id,
                action_id,
                action_type,
                actor,
                target,
                iso_now(),
                result,
                json.dumps(json_safe(detail or {}), separators=(",", ":")),
            ),
        )
        return action_id

    # --------------------------------------------------------------- preflight
    def save_preflight(self, mission_id: str, checks: list[dict[str, Any]]) -> None:
        for check in checks:
            self.execute(
                """INSERT OR REPLACE INTO preflight_checks
                   (mission_id, check_id, scope, result, measured, threshold, evidence_source, timestamp, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mission_id,
                    check["check_id"],
                    check.get("scope", "FLEET"),
                    check["result"],
                    str(check.get("measured", "")),
                    str(check.get("threshold", "")),
                    check.get("evidence_source", "AUTOMATED"),
                    check.get("timestamp") or iso_now(),
                    check.get("detail", ""),
                ),
            )

    def get_preflight(self, mission_id: str) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM preflight_checks WHERE mission_id = ? ORDER BY check_id", (mission_id,))

    # ---------------------------------------------------------------- survivors
    def upsert_survivor(self, survivor: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO survivors
               (survivor_id, mission_id, first_seen, last_seen, lat, lon, uncertainty_m, confidence,
                status, source_aircraft, drop_lat, drop_lon, observations, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(mission_id, survivor_id) DO UPDATE SET
                 last_seen=excluded.last_seen, lat=excluded.lat, lon=excluded.lon,
                 uncertainty_m=excluded.uncertainty_m, confidence=excluded.confidence,
                 status=excluded.status, drop_lat=excluded.drop_lat, drop_lon=excluded.drop_lon,
                 observations=excluded.observations, updated_at=excluded.updated_at""",
            (
                survivor["survivor_id"],
                survivor["mission_id"],
                survivor.get("first_seen"),
                survivor.get("last_seen"),
                survivor.get("lat"),
                survivor.get("lon"),
                survivor.get("uncertainty_m"),
                survivor.get("confidence"),
                survivor.get("status"),
                survivor.get("source_aircraft"),
                survivor.get("drop_lat"),
                survivor.get("drop_lon"),
                survivor.get("observations", 1),
                survivor.get("updated_at") or iso_now(),
            ),
        )

    def insert_observation(self, observation: dict[str, Any]) -> None:
        self.execute(
            """INSERT OR REPLACE INTO survivor_observations
               (observation_id, mission_id, survivor_id, aircraft, source_time, frame_count, confidence,
                model_version, calibration_version, lat, lon, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation["observation_id"],
                observation.get("mission_id"),
                observation["survivor_id"],
                observation.get("aircraft"),
                observation.get("source_time"),
                observation.get("frame_count"),
                observation.get("confidence"),
                observation.get("model_version"),
                observation.get("calibration_version"),
                observation.get("lat"),
                observation.get("lon"),
                json.dumps(json_safe(observation.get("raw", {})), separators=(",", ":")),
            ),
        )

    def get_survivors(self, mission_id: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM survivors WHERE mission_id = ? ORDER BY first_seen", (mission_id,)
        )

    # -------------------------------------------------------------- assignments
    def upsert_assignment(self, assignment: dict[str, Any]) -> None:
        self.execute(
            """INSERT OR REPLACE INTO assignments
               (assignment_id, mission_id, survivor_id, callsign, eta_s, decision_json,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assignment["assignment_id"],
                assignment["mission_id"],
                assignment["survivor_id"],
                assignment["callsign"],
                assignment.get("eta_s"),
                json.dumps(json_safe(assignment.get("decision", {})), separators=(",", ":")),
                assignment["status"],
                assignment.get("created_at") or iso_now(),
                assignment.get("updated_at") or iso_now(),
            ),
        )

    def update_assignment_status(self, assignment_id: str, status: str, updated_at: str | None = None) -> None:
        self.execute(
            "UPDATE assignments SET status = ?, updated_at = ? WHERE assignment_id = ?",
            (status, updated_at or iso_now(), assignment_id),
        )

    def get_assignments(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM assignments WHERE mission_id = ? ORDER BY created_at", (mission_id,)
        )
        for row in rows:
            try:
                row["decision"] = json.loads(row.pop("decision_json") or "{}")
            except json.JSONDecodeError:
                row["decision"] = {}
        return rows

    # -------------------------------------------------------------------- kits
    def upsert_kit(self, kit: dict[str, Any]) -> None:
        self.execute(
            """INSERT OR REPLACE INTO kits
               (kit_id, mission_id, callsign, load_status, reserved_for, release_command_id, final_status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kit["kit_id"],
                kit["mission_id"],
                kit["callsign"],
                kit["load_status"],
                kit.get("reserved_for"),
                kit.get("release_command_id"),
                kit.get("final_status"),
                kit.get("updated_at") or iso_now(),
            ),
        )

    def get_kits(self, mission_id: str) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM kits WHERE mission_id = ? ORDER BY kit_id", (mission_id,))

    def record_payload_event(self, event: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO payload_events
               (mission_id, event_id, kit_id, survivor_id, callsign, command, beam_result, result, timestamp, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.get("mission_id"),
                event.get("event_id"),
                event.get("kit_id"),
                event.get("survivor_id"),
                event.get("callsign"),
                event.get("command"),
                event.get("beam_result"),
                event.get("result"),
                event.get("timestamp") or iso_now(),
                json.dumps(json_safe(event.get("raw", {})), separators=(",", ":")),
            ),
        )

    # ------------------------------------------------------------------ safety
    def record_safety_event(self, event: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO safety_events
               (mission_id, event_id, severity, rule_id, callsign, measured_state, action, timestamp, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.get("mission_id"),
                event.get("event_id"),
                event.get("severity"),
                event.get("rule_id"),
                event.get("callsign"),
                event.get("measured_state"),
                event.get("action"),
                event.get("timestamp") or iso_now(),
                event.get("detail"),
            ),
        )

    def record_connection_event(self, event: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO connection_events
               (mission_id, endpoint, callsign, state, event_time, diagnostics_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event.get("mission_id"),
                event.get("endpoint"),
                event.get("callsign"),
                event.get("state"),
                event.get("event_time") or iso_now(),
                json.dumps(json_safe(event.get("diagnostics", {})), separators=(",", ":")),
            ),
        )

    def record_video_session(self, session: dict[str, Any]) -> None:
        self.execute(
            """INSERT INTO video_sessions
               (mission_id, feed_id, callsign, receiver, fingerprint, recording_path,
                start_time, end_time, health_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.get("mission_id"),
                session.get("feed_id"),
                session.get("callsign"),
                session.get("receiver"),
                session.get("fingerprint"),
                session.get("recording_path"),
                session.get("start_time"),
                session.get("end_time"),
                json.dumps(json_safe(session.get("health", {})), separators=(",", ":")),
            ),
        )

    # ------------------------------------------------------------------- replay
    def replay_data(self, mission_id: str) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if not mission:
            return {}
        callsigns = [row["callsign"] for row in self.query(
            "SELECT DISTINCT callsign FROM telemetry_samples WHERE mission_id = ?", (mission_id,)
        )]
        tracks = {callsign: self.telemetry_track(mission_id, callsign) for callsign in callsigns}
        events = self.get_events(mission_id, limit=2000)
        return {
            "mission": mission,
            "tracks": tracks,
            "events": list(reversed(events)),
            "survivors": self.get_survivors(mission_id),
            "assignments": self.get_assignments(mission_id),
            "kits": self.get_kits(mission_id),
        }
