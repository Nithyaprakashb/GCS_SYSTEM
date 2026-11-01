# RescueSwarm GCS — HTTP and WebSocket API

The backend is a loopback-only FastAPI service (`127.0.0.1:8765` by default). It
serves the Electron renderer and the optional browser dev UI on the same origin
policy. There is no authentication layer because the bind address is loopback and
the Host header is allow-listed; there is **no remote access mode**.

- OpenAPI schema: `GET /docs` (FastAPI built-in)
- All JSON request bodies are validated by Pydantic; unknown fields are ignored.
- Operator identity is read from the optional `X-Operator` header (default
  `operator`, truncated to 64 characters). It is recorded on every operator
  action; it is not an authentication mechanism.
- Every mutating route writes an operator action or event with
  `data_quality` of `SYSTEM`, `SIMULATED` or `LIVE`.
- Errors use `{"detail": "..."}` with these status codes:
  `400` validation/state error, `403` mode restriction, `404` unknown object,
  `409` executing mission cannot be reset, `500` unexpected error.
- No endpoint accepts shell commands, file paths from the renderer, or raw
  MAVLink frames. The copilot endpoint is read-only.

Evidence labels used in responses: `DESIGNED`, `CALCULATED`, `ASSUMED`, `BUILT`,
`SIMULATED`, `PHYSICALLY TESTED`, `PENDING TEST`. A simulated response says so
in `mode`, `data_quality` or `evidence`; it is never labelled LIVE.

---

## 1. Health and state

### `GET /api/health`

```json
{
  "ok": true,
  "version": "1.0.0",
  "mode": "SIMULATION",
  "phase": "SETUP",
  "mission_id": null,
  "offline": true
}
```

`offline` is always `true`; the service has no outbound network dependency.

### `GET /api/state`

Full UI snapshot, the same document pushed at WebSocket connect time. Top-level
keys:

| Key | Contents |
|---|---|
| `app` | version, `started_at`, `mode`, `telemetry_mode`, `offline`, `data_dir` |
| `mission` | `mission_id`, name, mode, phase, outcome, timestamps, elapsed, plan summary, upload receipts, recall, simulation snapshot |
| `fleet` | per-aircraft telemetry, link state, freshness, identity, command transactions |
| `safety` | evaluated warnings, active recall, pad status, separation results |
| `survivors` | registry with status, confidence, uncertainty, kit demand |
| `kits` | ten-kit ledger with `load_status`, `final_status`, reservation |
| `allocations` | assignment table with operator-facing status |
| `deliveries` | per-task delivery state machine |
| `video` | per-feed health, recording path, latency/loss counters |
| `preflight` | manual sign-off items and the last automatic report |
| `events` | most recent 80 events |
| `generated_at` | server timestamp |

The frontend treats this snapshot as read-only truth. It never derives a
"delivered" state from a local click.

---

## 2. System

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/system/info` | OS, Python, dependency versions, ffmpeg path, disk free |
| GET | `/api/system/diagnostics` | checks for data directory, database, ffmpeg, recordings, serial ports |
| GET | `/api/system/devices` | enumerated serial/video devices; `is_modem_candidate` is a hint only |
| GET | `/api/system/config` | effective configuration with secrets redacted (there are none) |
| PATCH | `/api/system/config` | validated patch; broadcasts `CONFIG_UPDATE` |
| POST | `/api/system/mode` | `{"mode": "SIMULATION"}` or `{"mode": "REAL"}` |
| POST | `/api/system/shutdown` | Close the station's own services; requires `{"confirmation": "SHUTDOWN"}`, a non-empty `X-Operator` header and an optional `reason` |

`POST /api/system/shutdown` is what the Electron shell calls before it exits, because on
Windows `child.kill()` is `TerminateProcess` — it skips Python teardown, leaves `ffmpeg`
grandchildren holding the capture device, and leaves the event queue unwritten. Guards: the
typed token, the operator header (a cross-origin form POST cannot set one without a CORS
preflight this server refuses), and a `409` while `mission.phase == "EXECUTING"` — the station
must not close with aircraft airborne. It closes only what it owns: **no endpoint stops an
onboard autopilot protection, and shutdown never sends a flight command.** The shell escalates
to a process-tree kill if this call never returns. Tested in `tests/test_station_shutdown.py`.

`PATCH /api/system/config` body:

```json
{"patch": {"mission": {"merge_distance_m": 5.0}}}
```

Only existing keys may be updated. Transport changes require restarting the
telemetry or video service; the response says so.

`POST /api/system/mode` switches mission and telemetry mode together. Switching
to REAL while a mission is executing is refused. Changing mode never fabricates
a radio link: REAL mode still requires a configured endpoint and a heartbeat.

---

## 3. Fleet and telemetry

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/fleet` | fleet snapshot plus current safety evaluation |
| POST | `/api/fleet/connect` | body `{"mode": "SIMULATION"\|"REAL"}` |
| POST | `/api/fleet/disconnect` | closes transports, marks links lost |

REAL connect requires `telemetry.shared_endpoint` (shared multipoint topology)
or all three `telemetry.independent_endpoints` (independent P2P topology). A
missing endpoint is a `400`. Detection of a USB serial port never counts as a
connection, pairing, or verified topology.

Fleet fields per aircraft include: `connected`, `stale`, `link`, `source`,
`data_quality`, `identity_valid`, `identity_note`, `battery_pct`,
`battery_voltage`, `gps_fix`, `gps_sats`, `ekf_ok`, `mode`, `armed`, `lat`,
`lon`, `alt_rel_m`, `agl_m`, and `heartbeat_age_s`. In SIMULATION every field is
sourced from the labelled simulator.

---

## 4. Video

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/video/health` | per-feed state, mapping, recording, fault counters |
| POST | `/api/video/start` | optional `{"mode": "SIMULATION"\|"REAL"}`; defaults to mission mode |
| POST | `/api/video/stop` | stops capture and finalises recordings |
| GET | `/api/video/{callsign}/frame.jpg` | latest JPEG frame (503 when none) |
| GET | `/api/video/{callsign}/stream` | MJPEG stream (`multipart/x-mixed-replace`) |
| POST | `/api/video/{callsign}/inject` | `{"fault": "freeze"\|"black"\|"loss"\|null}`; SIMULATION only |

Feeds are mapped by callsign from `config/fleet.json`. A feed with
`mapped: false` and a null fingerprint is reported as unmapped; the GCS does not
guess which capture device belongs to which receiver. RunCam video never carries
YOLO overlays.

---

## 5. Mission workflow

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/mission/status` | current mission status and plan summary |
| GET | `/api/mission/manual-items` | preflight items that require a human signature |
| POST | `/api/mission/import-kml` | `{"kml_text": "...", "filename": "..."}` or `{"sample": true}` |
| POST | `/api/mission/load-sample` | imports `samples/sample_search_zone_10ha.kml` |
| POST | `/api/mission/plan` | deterministic lawnmower plan for the imported boundary |
| GET | `/api/mission/preflight` | last automatic preflight report |
| POST | `/api/mission/preflight` | run automatic preflight now |
| POST | `/api/mission/sign` | `{"check_id": "...", "signed": true}` |
| POST | `/api/mission/sign-all-sim` | SIMULATION only: signs every manual item |
| POST | `/api/mission/upload` | upload and read-back verify; SIMULATION returns labelled simulated receipts |
| GET | `/api/mission/gate` | readiness gate; the only unlock for START |
| POST | `/api/mission/start` | body `{"confirmation": "START"}` |
| POST | `/api/mission/abort` | `{"reason": "..."}`; controlled abort to LOITER |
| POST | `/api/mission/recall` | emergency recall to the pad and land |
| POST | `/api/mission/recall/clear` | clears recall flag only when it is safe to do so |
| POST | `/api/mission/reset` | clears a COMPLETE/ABORTED working session; 409 while EXECUTING |

Import validates that the KML contains exactly one usable polygon, that the area
is between 1 000 m² and 1 000 000 m², and that coordinates are within WGS84.
Plan generation rejects boundaries whose usable width cannot produce a sane lane
set. The plan response includes `plan_id`, `plan_hash`, lane geometry, geofences,
energy estimate, delivery profile and validation flags.

`POST /api/mission/start` requires all of:

1. A `mission_id` and a valid plan.
2. Upload and read-back verified for all three aircraft (real receipts in REAL
   mode; simulated receipts are refused when mode is REAL).
3. Fresh, identified telemetry matching the mission mode.
4. An accessible pad, ten free kits, no active recall.
5. A completed preflight with all manual items signed.
6. The literal confirmation token `START`.

Any unmet condition returns `400` and emits `mission.start_denied`. There is no
force-start route.

---

## 6. Survivors, kits, allocations, deliveries

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/survivors` | registry snapshot |
| GET | `/api/survivors/{survivor_id}` | one survivor (404 if unknown) |
| GET | `/api/kits` | ten-kit ledger |
| GET | `/api/allocations` | assignment table |
| GET | `/api/deliveries` | delivery task state machine |
| GET | `/api/events?limit=100` | recent bus events (1–500) |

Survivors are created only from a report that passes GCS re-validation
(confidence ≥ 0.85, ≥ 3 frames, inside the AOI, valid coordinates). Reports
within `merge_distance_m` (default 5 m) are merged, never duplicated. The
registry is read-only through the API; evidence arrives through MAVLink tunnel
events or the simulator.

Allocation status is operator-facing (`ASSIGNED`, `IN_DELIVERY`, `DELIVERED`,
`UNKNOWN`, `ABORTED`). Delivery states are internal (`QUEUED`, `CLIMB`,
`STANDBY`, …). `DELIVERED` requires a `CONFIRMED` break-beam result; a missing
beam yields `UNKNOWN` and never triggers an automatic retry.

---

## 7. Map

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/map/style` | MapLibre style JSON using local raster tiles only; the raster source's `maxzoom` is set from the tile-pack manifest |
| GET | `/api/map/manifest` | Basemap provenance: which surface is on screen, and where its data came from |
| GET | `/api/map/tiles/{z}/{x}/{y}.png?theme=dark\|light` | Local raster tile — from the OSM pack, an over-zoomed parent of one, or the procedural fallback |

The style contains no glyph URL, no sprite URL and no external source. There are
no internet map tiles at any zoom. Themes are cached separately.

Lookup order for a tile: an exact cached tile, then the covered parent from the pack
(`x >> shift`, `y >> shift` at the pack's top zoom), then the procedural renderer — whose
output is written to disk and stamped as invented. The over-zoom step is the reason the
style's `maxzoom` stops at the pack top: a z17 request must return a magnified **real** tile,
not a tile the server had to imagine. Verified against the running station on 2026-11-01
(`z17 → 14 997 B` from the OSM parent; an uncovered tile → `46 311 B` procedural).

`GET /api/map/manifest` returns one of two shapes. With a pack installed — this is the real
answer from this station on 2026-11-01, abridged only where marked:

```json
{"kind": "openstreetmap-offline-pack",
 "label": "OpenStreetMap offline tile pack",
 "attribution": [
   "OpenStreetMap © OpenStreetMap contributors · ODbL · data as of 2026-10-30T21:24:51Z",
   "Offline tile pack (building, commercial, …, wood) · built 2026-11-01T10:16:03Z · outside the covered area the surface falls back to procedural reference"],
 "network_access": false,
 "pack_zooms": [10, 11, 12, 13, 14, 15, 16],
 "coverage_deg": [77.4, 12.75, 77.9, 13.25],
 "osm_data_date": "2026-10-30T21:24:51Z",
 "built_at": "2026-11-01T10:16:03Z",
 "layers": {"building": 251679, "road_minor": 166616, "road_major": 5961,
            "road_medium": 4167, "water": 13739, "residential": 10078, "…": "14 layers in all"},
 "simplifications": [
   "polygon interior rings (lake islands, courtyards) are filled, not punched",
   "no street or place labels",
   "geometry culled by bounding box, not clipped to tile edges"],
 "note": "Tiles inside the recorded boxes are rendered from OpenStreetMap data. Anywhere else the surface is the procedural reference."}
```

Without one — and also when the manifest is missing or unparseable, which is the deliberate
safe side — the same endpoint answers `kind: "procedural"`, `label: "Procedural reference
surface"`, empty `pack_zooms`, `coverage_deg: null`, `osm_data_date: null` and a note telling the
operator to run `tools/offline_map.py extract` and `build`. `network_access` is `false` in both
shapes; nothing in this service opens a socket for a tile.

`coverage_deg`, `layers` and `simplifications` come from the pack manifest, so a rebuilt pack
changes what the station claims about it. The manifest is re-read when the file's size or mtime
changes, so no restart is needed. `attribution` is what the style shows. Tested in
`tests/test_offline_map.py` (25 tests), including the corrupt-manifest and mtime cases.

---

## 8. History, replay, analytics and reports

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/missions?limit=30` | archived missions |
| GET | `/api/missions/{id}` | mission record, plan, survivors, assignments, kits, events |
| GET | `/api/missions/{id}/events?limit=500` | recorded events |
| GET | `/api/missions/{id}/replay` | time-ordered replay frames |
| GET | `/api/missions/{id}/analytics` | computed mission analytics |
| POST | `/api/missions/{id}/report` | regenerate report from recorded data |
| GET | `/api/missions/{id}/report` | report JSON |
| GET | `/api/missions/{id}/report.pdf` | dependency-free PDF report |

Reports are generated from evidence already on disk. Regenerating a report for
an old mission never labels an inferred value as measured.

---

## 9. Copilot (read-only)

`POST /api/copilot/ask`

```json
{"question": "Why was DEL-01 assigned to SURV-004?"}
```

The deterministic, offline copilot answers from the current snapshot. It has no
tool access, cannot send MAVLink, cannot release a kit, and does not require an
online LLM (`copilot.llm_enabled` is `false` by default). An optional local
Ollama model may be enabled for phrasing, but only over the same read-only
context; no LLM output is ever executed.

---

## 10. Simulation fault injection

`POST /api/simulation/inject`

```json
{"fault": "battery", "callsign": "DEL-02", "value": 17}
```

Available only when the mission mode is SIMULATION (`403` otherwise). Supported
faults are defined by `SimulationFleet.inject` (battery, GPS loss, link loss,
freeze, survivor report, break-beam failure, and similar). Injected faults are
labelled `SIMULATED` and never presented as flight data.

---

## 11. WebSocket `/ws`

On connect the server sends one snapshot:

```json
{"kind": "snapshot", "data": { ...same shape as GET /api/state... }}
```

Then it streams every published event:

```json
{"kind": "event", "event": {"event_id": "...", "topic": "mission.phase",
 "severity": "info", "source": "MISSION", "source_time": "...",
 "receive_time": "...", "payload": {}, "data_quality": "SIMULATED"}}
```

A `{"kind": "ping"}` message is sent after 20 s of silence. The client reconnects
with backoff and refetches `/api/state` after a disconnect; it does not trust an
event stream as a complete state source. WebSocket clients are receive-only;
operator actions are HTTP POSTs.

---

## 12. Deliberately absent endpoints

There is no route for:

- arbitrary MAVLink frames or shell commands;
- forcing START, bypassing the readiness gate, or overriding a safety interlock;
- releasing a payload without the ledger state machine;
- enabling internet tiles or cloud sync;
- disabling ArduPilot failsafes (the GCS only writes documented fence and
  battery failsafe parameters during upload).

Absence is part of the safety design, not an unfinished feature.
