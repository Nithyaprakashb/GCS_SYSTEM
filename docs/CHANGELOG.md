# Changelog — RescueSwarm Ground Control Station

All notable changes to this station are documented here. The format follows
Keep-a-Changelog; versions are the application version reported by
`/api/health` (`config.APP_VERSION`, currently `2.0.0`) and by `desktop/package.json`.

Every entry carries the evidence label of how the change was demonstrated, because on a
ground control station "it compiles" and "it flew" are different claims:

`BUILT` — implemented and read back from source · `SIMULATED` — exercised through
`backend/tests/` or `tools/sim_e2e.py` · `CALCULATED` — derived from a model, not measured
· `PENDING TEST` — implemented, requires hardware or a bench · `DESIGNED` — specified, not
implemented.

**No entry in this file is `PHYSICALLY TESTED`.** No ArduPilot vehicle, RFD868x modem or
TX800 receiver has been connected to this station.

---

## [2.0.0] — Independent review cycle, 2026-11-01

The station was re-reviewed end-to-end as an existing codebase rather than a new project:
independent audit, UI/UX pass, MAVLink and communication pass, video pass, safety and
failure analysis, architecture review, and a test matrix. Nothing was rewritten wholesale;
every change below is a correction to working code or an addition to it.

### Fixed — critical safety and command path

- **An acknowledged command could never settle** (`telemetry.py`). `ACKNOWLEDGED` was in
  the terminal set the sweep used, so `ACK_PENDING` never resolved and a payload release
  could not complete. `TERMINAL_TX_STATES` and `SETTLING_TX_STATES` are now separate, and
  the lifecycle cannot leave a terminal state. `SIMULATED` — `test_command_lifecycle.py`.
- **Independent (per-aircraft) telemetry topology discarded every inbound message**
  (`telemetry.py` `_on_mavlink`). A correctly wired point-to-point link reported the
  aircraft offline and refused every command. The interlock is now the sysid/endpoint
  *mismatch* check only. `SIMULATED` — same file.
- **A release frame that had left the station could be counted back into the magazine** and
  promised to a second survivor (`telemetry.py`, `delivery.py`, `ledger.py`,
  `mission_engine.py`). `TRANSMITTED` is now irreversible; an abandoned command settles as
  `UNKNOWN`. `SIMULATED` — 3 tests.
- **Payload release was not blocked while emergency recall was active** (`safety.py`
  `release_clearance`). Recall now latches before it transmits, and the clearance gate
  refuses against it. `SIMULATED`.
- **A `COMMAND_ACK` from a companion component (camera, gimbal) was accepted as autopilot
  acceptance** (`telemetry.py` `_on_command_ack`). Non-autopilot ACKs are ignored.
  `SIMULATED`.
- **A link change mislabelled an accepted command as a plain timeout**
  (`telemetry.py` `_abandon_pending_transactions`); the recorded reason now reflects what
  actually happened. `SIMULATED`.
- **The simulated fleet could not start after the event-loop fix** (`simulation.py`,
  `events.py`): `asyncio.create_task` on a worker thread has no running loop, so the
  coroutine was never awaited and the mission sat at `EXECUTING` with nothing flying it.
  Found by the end-to-end run, not by the unit suite. `SIMULATED` — `test_threading.py`.

### Fixed — core features

- Blocking mission work (mission start, recall, reset, video stop) was moved off the event
  loop with `asyncio.to_thread`, so one slow lifecycle call can no longer stall every
  request (`api.py`). `BUILT`.
- **A feed was declared `LIVE` before a single frame existed**, and preflight's
  `fleet.video` gate accepted it (`video.py`, `preflight.py`). `SIMULATED`.
- **Recording accepted a frameless feed and left the `video_sessions` evidence row open**
  (`video.py`, `db.py`). `SIMULATED` — 4 tests; the test that had pinned the wrong
  behaviour was itself corrected.
- **Video start and stop were not mutually exclusive**, so an orphan `ffmpeg` kept the
  DirectShow device claimed (`video.py` `_lifecycle_lock`). `SIMULATED`.
- Mission abort answered the client with `None` instead of a body
  (`mission_engine.py`). `SIMULATED`.
- Read-back verification, fence radius handling and survivor caps were tightened
  (`upload.py`, `survivors.py`) with the profile as the single source of the limits.
  `SIMULATED`.

### Fixed — state machines

- The frontend delivery spine disagreed with the backend machine (missing `ACK_PENDING`,
  `CONFIRMED`, `UNKNOWN`, `ABORTED`; `HOLD_CLEARANCE` misplaced). The names now come from
  `delivery.py` `STATES` (`MissionScreen.tsx`). `BUILT`.
- A delivered survivor could be reopened by a late report, and a merge could move a geotag
  an aircraft was already committed to (`survivors.py`). `SIMULATED`.
- Beam evidence could mark a kit delivered without a release and could be applied twice
  (`ledger.py`, plus an idempotency index in `db.py`). `SIMULATED`.

### Added — offline OpenStreetMap basemap

- New build pipeline `tools/offline_map.py` (extract → build → status) and
  `tools/osm_basemap.py` (deterministic mercator geometry, tile rasteriser). A Geofabrik
  India extract is read with pyrosm 0.28 out-of-core and rendered to 1,572 tiles across
  z10–z16 in dark and light themes (469,124 features, 16.5 MiB, 51 s), served entirely from
  disk. `BUILT`.
- `MapTileService` now reads the tile pack before falling back to its procedural surface,
  serves an over-zoom request by up-scaling the covered parent instead of inventing
  terrain, and publishes provenance (`map_tiles.py`). The style's source `maxzoom` tracks
  the pack so clients over-zoom a real tile rather than requesting one that never existed.
  `SIMULATED` — `test_offline_map.py` (25 tests; a mutation check confirmed the over-zoom
  test fails when the lookup is removed).
- New endpoint `GET /api/map/manifest`: source, OSM data date, coverage boxes, zoom range,
  feature counts per layer, deliberate simplifications and `network_access: false`. `BUILT`.
- The `.pbf` and the generated tiles stay out of the repository; the pack is reproducible
  from two documented commands.

### Changed — UI honesty and reachability

- Mission limits shown during setup are read from the plan and the profile, not hard-coded.
  The backend publishes them as `mission.limits` from the same keys `safety.py` and
  `survivors.py` consult; absent values render `—`. Previously the screen advertised
  `12 m AGL`, `8 m/s`, a `5 m` merge radius and a `20 m` release clearance as literals. `BUILT`.
- The map's telemetry chip reports the fleet's actual `data_quality` (`evidenceQuality` in
  `lib/format`, shared by setup and mission screens) instead of a value derived from the
  mode label, and renders `STALE`, `LOST`, `MIXED (…)`, `NO TELEMETRY` and `RECORDED` with
  their own wording. `BUILT`.
- `ABORT` and `EMERGENCY RECALL` stay reachable when a precondition is unmet: the buttons
  are marked `.is-blocked` with `aria-disabled` and a reason in a toast, instead of carrying
  the native `disabled` attribute that swallowed the press. `BUILT`.
- Confirmation dialogs are named for assistive technology (`aria-labelledby` / `aria-label`,
  `aria-describedby`), trap focus, restore focus on close, and `Escape` now closes only the
  topmost of stacked dialogs. `BUILT`.
- The replay report link no longer falls back to a dead `href="#"` styled as a primary
  button. `BUILT`.
- `prefers-reduced-motion` collapses the radar sweep, blips, spinners and slide-ins to
  their end state without hiding any information. `BUILT`.
- The boot screen's unproven check no longer uses a tick glyph in amber (`TriangleAlert`),
  and the header fleet dot actually changes colour. `BUILT`.
- The command lifecycle is now visible to the operator: state, attempts, expectation and
  terminal reason per command (`MissionScreen.tsx`). `BUILT`.

### Added — tests and documentation

- 118 backend tests in 11 files (was 93 in 5), including the new `test_offline_map.py`,
  `test_command_lifecycle.py`, `test_threading.py`, `test_station_shutdown.py`,
  `test_survivor_registry.py`, `test_upload_readback.py` and `test_video_capture.py`.
  `SIMULATED`.
- `docs/audit/`: `INDEPENDENT_AUDIT.md`, `UI_UX_REVIEW.md`, `ARCHITECTURE_REVIEW.md`,
  `MAVLINK_COMMUNICATION_REVIEW.md`, `VIDEO_SYSTEM_REVIEW.md`,
  `SAFETY_AND_FAILURE_ANALYSIS.md`, `TEST_RESULTS.md`, `PRIORITISED_FIXES.md`,
  `REQUIREMENTS_TRACEABILITY.md`.
- Defect IDs are namespaced across the review documents and joined by a cross-reference
  table in `PRIORITISED_FIXES.md`; `MAVLINK_COMMUNICATION_REVIEW.md` uses `ML-n` so its
  list no longer collides with the station-wide `M-n` series.
- `REQUIREMENTS_TRACEABILITY.md` walks all 66 FR rows and 12 NFR rows from the reference
  PRD/TRD against the code as it now stands: **MET 41 · MET, HARDWARE UNVERIFIED 12 ·
  PARTIAL 19 · NOT IMPLEMENTED 6** (FR-016 GCS heartbeat, FR-032 ArduPilot pre-arm read,
  FR-093 telemetry trend detection, NFR-002 durability across restart, NFR-003 process
  isolation, NFR-006 bounded retries). That pass opened seven new fix-list rows
  (`32b`, `42`–`47`) in `PRIORITISED_FIXES.md`, each naming the requirement it blocks.
- `README.md` rewritten for this cycle: executed regression numbers, the offline-map
  build recipe and its provenance, the two requirements the station does *not* meet at
  the command path (`HEARTBEAT`, retries), and links into `docs/audit/`.
- `docs/API.md`: documents `POST /api/system/shutdown`, which existed and was tested but
  was absent from the route table, and `GET /api/map/manifest` with its real response
  shape, tile lookup order and over-zoom rule.
- `tools/offline_map.py build` re-run through the shipped path: geometry to
  `map/build/basemap`, the stale `data/map_tiles/basemap` intermediate removed, 1,572
  tiles with feature counts identical to the previous pack (deterministic). Recorded in
  `TEST_RESULTS.md` §1.3.

### Known limitations carried forward (not regressions)

- `PENDING TEST` — servo channel/PWM mapping, RFD868x topology (multipoint vs three
  independent modems), TX800 capture enumeration, real fence behaviour, glass-to-glass
  video latency. The station refuses to claim any of them; `payload_hardware_verified` and
  `topology_verified` are `false`/protected.
- `DESIGNED, NOT PROVISIONED` — the onboard comm-loss failsafe. The station transmits no
  MAVLink `HEARTBEAT`, so `FS_GCS_ENABLE` has nothing to observe; provisioning it before a
  heartbeat sender exists would make link loss more dangerous, not less. See finding
  `H-6` / `A-2`.
- `OPEN` by decision — commands are single-shot (`attempts` stays 1, `RETRYING` is never
  set); transactions are not persisted across a restart; a survivor left `UNSERVED` by an
  abort is not re-allocated automatically; the log screen reads the bounded in-memory ring.
- `MAV_CMD_DO_SET_SERVO` (183) has no `SERVO_OUTPUT_RAW` expectation: a release is proven
  by beam evidence and the ledger, not by a servo read-back. Deliberate, documented.

**This build is not flight-ready.** It is a station that has been reviewed, corrected and
tested against a simulator, and that tells the operator which of its claims the simulator
could not prove.

---

## [1.0.0] — Initial station build

Offline-first ground control station: FastAPI backend with a SQLite WAL evidence store,
pymavlink transport with a per-endpoint reader thread, deterministic KML lawnmower planner
with read-back verification, safety and preflight gates, kit ledger with beam-confirmed
delivery, allocation and delivery engines, TX800 video pipeline via `ffmpeg`/OpenCV with
recording and health monitoring, replay and PDF reporting, an advisory-only local copilot,
a React 19 + MapLibre interface with procedural offline tiles, and an Electron shell that
spawns and supervises the backend with no external network access.
