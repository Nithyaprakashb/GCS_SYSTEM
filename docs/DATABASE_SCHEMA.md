# Database Schema

**Engine:** SQLite 3.50.4 (WAL, foreign keys on)
**File:** `data/rescueswarm.db` (or `%APPDATA%/RescueSwarm GCS/data` when packaged)
**Status:** BUILT · SIMULATED

The database is the mission evidence source. The UI is a view over the same state the
engines use; nothing is reconstructed from screenshots or browser storage.

---

## Entity overview

```
missions 1─┬─ telemetry_samples
           ├─ preflight_checks
           ├─ operator_actions
           ├─ mission_plans
           ├─ survivors 1─── survivor_observations
           ├─ assignments 1── kits
           ├─ payload_events
           ├─ safety_events
           ├─ video_sessions
           └─ mission_events
settings (global key/value)
```

Survivors and assignments are scoped by `mission_id`. Importing a new KML creates a
new mission ID; the previous mission and its rows remain intact.

---

## Tables

### `missions`
Mission header and lifecycle.

| Column | Type | Notes |
|---|---|---|
| `mission_id` | TEXT PK | `MSN-YYYYMMDD-HHMMSS-XXXX` |
| `name` | TEXT | From KML document name or filename |
| `mode` | TEXT | `SIMULATION` or `REAL` |
| `phase` | TEXT | `SETUP`, `PLANNING`, `INSPECTION`, `PREFLIGHT`, `UPLOAD`, `READY`, `EXECUTING`, `COMPLETE`, `ABORTED` |
| `state` | TEXT | `PREPARING`, `COMPLETED`, `ABORTED` |
| `created_at`, `started_at`, `ended_at` | TEXT | ISO-8601 UTC |
| `config_version`, `plan_hash`, `outcome`, `notes` | TEXT | Config profile, plan digest, `SIMULATION_COMPLETE`, `EMERGENCY_RECALL_COMPLETE`, `ABORTED` |

### `telemetry_samples`
Downsampled flight evidence (one row per second per aircraft).

`mission_id, callsign, source_time, receive_time, lat, lon, alt_rel_m, agl_m, speed_ms, heading_deg, battery_pct, mode, armed, quality`.

### `connection_events`
Endpoint, callsign, state (`CONNECTED`, `ERROR`, `LOST`) and diagnostics JSON. Used to
prove that a link was never reported healthy merely because a port existed.

### `preflight_checks`
`(mission_id, check_id)` primary key; `result` is `PASS` / `FAIL` / `MANUAL` / `PENDING`;
`measured`, `threshold`, `evidence_source` and `timestamp` capture why the result was
produced.

### `operator_actions`
Every explicit operator action: `action_id`, `action_type` (`KML_IMPORT`, `PLAN_GENERATE`,
`PREFLIGHT_RUN`, `MISSION_START`, `MISSION_ABORT`, `RECALL`, `RECALL_CLEAR`, …), `actor`,
`target`, `timestamp`, `result`, `detail_json`.

### `mission_plans`
`plan_id`, `mission_id`, `kml_hash`, `plan_hash`, full `plan_json`, `created_at`,
`upload_state`. The stored plan is the exact plan that was uploaded, not a re-generation.

### `survivors`
Primary key `(mission_id, survivor_id)`.

`survivor_id, mission_id, first_seen, last_seen, lat, lon, uncertainty_m, confidence,
status, source_aircraft, drop_lat, drop_lon, observations, updated_at`.

Status: `CONFIRMED`, `ASSIGNED`, `IN_DELIVERY`, `DELIVERED`, `UNKNOWN`, `UNSERVED`,
`DUPLICATE`, `REJECTED`.

### `survivor_observations`
One row per accepted frame group: `observation_id`, `mission_id`, `survivor_id`,
`aircraft`, `source_time`, `frame_count`, `confidence`, `model_version`,
`calibration_version`, `lat`, `lon`, `raw_json`.

### `assignments`
Allocation decision and current lifecycle.

`assignment_id, mission_id, survivor_id, callsign, eta_s, decision_json, status,
created_at, updated_at`.

`decision_json` stores the complete candidate list, rejections, selected aircraft,
selected reason, tie-break, distance, queue depth, service time and transit speed.
`status` is the operator-facing lifecycle: `ASSIGNED`, `IN_DELIVERY`, `DELIVERED`,
`UNKNOWN`, `ABORTED` (not the internal delivery machine state).

### `kits`
Shared ledger: `kit_id`, `mission_id`, `callsign`, `load_status`, `reserved_for`,
`release_command_id`, `final_status`, `updated_at`.

`load_status`: `LOADED`, `RESERVED`, `RELEASE_SENT`, `DELIVERED`, `UNKNOWN`.
`final_status`: `DELIVERED`, `UNKNOWN`, `RETURNED`, `LOST`.
`kit_id` is unique; `reserved_for` is one survivor at most.

### `payload_events`
Release audit: `event_id`, `kit_id`, `survivor_id`, `callsign`, `command`,
`beam_result`, `result`, `timestamp`, `raw_json`.

### `safety_events`
`severity`, `rule_id`, `callsign`, `measured_state`, `action`, `timestamp`, `detail`.

### `video_sessions`
`feed_id`, `callsign`, `receiver`, `fingerprint`, `recording_path`, `start_time`,
`end_time`, `health_json`.

### `mission_events`
Append-only event stream used by the live log and replay: `event_id`, `topic`,
`severity`, `source`, `source_time`, `receive_time`, `payload_json`, `data_quality`.

### `settings`
Key/value JSON overrides for operator-editable configuration, with `updated_at`.

---

## Evidence files

Each mission gets a folder under `data/Missions/<mission_id>/`:

```
manifest.json               mission identity, mode, config version, hashes
plan/aoi.json               validated boundary and AOI metadata
plan/plan.json              exact generated plan
plan/upload_receipts.json   per-aircraft upload and read-back receipts
survivors/allocations.jsonl one JSON object per allocation decision
video/<CALLSIGN>.mp4        independent recording per receiver (SIMULATED synthetic frames)
reports/report.json|.md|.pdf
reports/completion_summary.json
events.jsonl                append-only event mirror
```

The PDF is generated by a dependency-free writer in `reporting.py`; it does not
require ReportLab.

---

## Migration notes

`Database._migrate()` upgrades an older deployments' single-key `survivors` table to the
composite `(mission_id, survivor_id)` key and adds `mission_id` to
`survivor_observations`. The migration is idempotent and runs on startup.
