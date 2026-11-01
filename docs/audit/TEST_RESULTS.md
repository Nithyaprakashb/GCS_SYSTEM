# RescueSwarm GCS — Test Results

Date: 2026-10-31, with the **final regression re-run on 2026-11-01** after the last UI,
provenance and basemap changes. Machine: Windows, Python 3.14, Node 22, no external
hardware connected to this station. Every result below was produced by running the command
printed next to it; none is inferred.

Statuses used, as the brief required them to be distinguished:
**passed** · **failed** · **not implemented** · **not executable without hardware** ·
**not yet physically verified** (implemented, but the proof needs real aircraft, modems
or receivers).

---

## 1. Commands and their output

### 1.1 Final regression, 2026-11-01

| Command | Result |
|---|---|
| `python -m pytest tests -q` (in `backend/`) | **118 passed in 29.36 s** |
| `python -m pytest tests/test_offline_map.py -q` | **25 passed in 1.72 s** |
| `npx tsc --noEmit` (in `desktop/`) | **clean, exit code 0** (after the `ui.tsx`, `App.tsx`, `TacticalMap.tsx`, `SetupScreen.tsx`, `ReplayScreen.tsx`, `format.ts`, `types.ts`, `GcsContext.tsx` edits) |
| `npm run build` (in `desktop/`) | **built in 10.74 s**; `dist/assets/index-*.js 1 422.42 kB │ gzip 397.45 kB`, `index-*.css 127.79 kB │ gzip 26.17 kB`; rollup chunk-size warning (>500 kB) — cosmetic, recorded |
| `python tools/sim_e2e.py --json` (run A, complete the sortie) | **16/16 checks pass**, `phase=COMPLETE`, `counts={"DELIVERED": 10}`, 10 evidence rows, `elapsed_s 84.9`, mission `MSN-20261101-095758-8DDC` |
| `python tools/sim_e2e.py --json --abort-after 8` (run B, abort mid-delivery) | **18/18 checks pass**, `phase=ABORTED`, `counts={"LOADED": 10}`, `returned=1 unverified=0`, one delivery left in `RETURNING`, `elapsed_s 8.6` |
| `python tools/offline_map.py build --zooms 10-16` | **1 572 tiles · 16.5 MiB · 51.3 s**, 469 124 features; z10 6 · z11 16 · z12 49 · z13 16 · z14 49 · z15 144 · z16 506 |
| `GET /api/map/style`, `/api/map/manifest`, `/api/map/tiles/{z}/{x}/{y}.png` | served from the running station: `kind = openstreetmap-offline-pack`, pack zooms `[10..16]`, style source `maxzoom 16`; a **z17 request returned the over-zoomed OSM parent (14 997 B)**, a z16 tile 16 431 B, and an uncovered tile the procedural reference (46 311 B) |

Run B aborted before any release completed, which is why every kit reads `LOADED` rather
than the mix recorded on 2026-10-31 (`DELIVERED 1, LOADED 9`). The two runs are different
experiments, not contradictory results: run A proves the delivery path closes, run B
proves an abort reconciles.

Stderr from both end-to-end runs was empty. The `[mpeg4] Invalid pts (487) <= last (487)`
warning recorded on 2026-10-31 was **not reproduced here, because `tools/sim_e2e.py` does
not start the video subsystem at all** — see coverage gap G-1. The duplicate-PTS artefact
stays recorded as a LOW finding observed in an earlier session's recording run.

### 1.2 Results carried from 2026-10-31

| Command | Result |
|---|---|
| `python -m pytest -q` (in `backend/`) | 93 passed in 27.18 s (pre-dates the 25 map tests and the UI/provenance work) |
| `python -m pytest tests/test_video_capture.py -q` | **13 passed in 18.13 s** |
| `python -m pytest tests/test_threading.py -q` | **2 passed in 2.06 s** |
| `python -m pytest tests/test_command_lifecycle.py -q` | 24 passed |
| `python -m rescueswarm.main` then `GET /api/health`, `/api/state` | `/api/state` carries `fleet.transactions`, video `no_frame_s`, `waiting_count`, `mission.time_cap_min` and, since this cycle, `mission.limits` |

---

### 1.3 Post-documentation re-run of the basemap build, 2026-11-01

The build was re-run after the audit documents were written, to prove the path the shipped code
uses rather than the path the earlier build happened to leave behind:

```
python tools/offline_map.py build --zooms 10-16
→ basemap 469,124 features -> map\build\basemap
→ cleared 1572 previously cached tile(s) at zoom [10..16]
→ done 1572 tiles · 16.0 MiB · manifest data\map_tiles\map_manifest.json
```

- Geometry now lands in `map/build/basemap` (the fixed location); the stale
  `data/map_tiles/basemap` intermediate from the 2026-10-31 build is **gone**, removed by the
  cleanup in `cmd_build`. `data/map_tiles/` now holds served tiles and the manifest only.
- The manifest's feature counts are **identical** to the 2026-10-31 pack (`building 251,679`,
  `road_minor 166,616`, `water 13,739`, …) from the same extract — the pipeline is deterministic,
  which is the property the mission-plan hash relies on. `built_at` is `2026-11-01T10:16:03Z`;
  `osm_data_date` is unchanged at `2026-10-30T21:24:51Z`.
- Tile sizes in the §1.1 HTTP row (z16 16,431 B, z17 14,997 B) were for the tile pair chosen in
  that run. Re-checked in-process against the rebuilt pack on the pad's own tiles:
  **`tile(17) returns byte-identical output to its `tile(16)` parent` (32,034 B = 32,034 B)**,
  the light-theme z17 is a separately rendered 17,123 B raster, and an uncovered tile is the
  46,311 B procedural reference. The invariant the mutation test guarded (over-zoom must never
  invent terrain) holds on the current pack.
- The build reported **16.0 MiB** here; §1.1 recorded **16.5 MiB** for the same zoom range on the
  same extract. Both figures are what the tool printed at the time; the difference was not
  investigated (tile byte totals shift with encoder state and with what else is in the output
  directory), and it is recorded rather than reconciled. The tile *count* agrees at 1,572.

- `python tools/offline_map.py build --zooms 10-16` re-run after the documentation pass, with
  the whole suite and the typechecker alongside it: **118 passed in 28.23 s**, `tsc --noEmit`
  exit 0. Same counts as §1.1; the two timings are two executions, not two results.
- `docs/API.md` and `api.py` were diffed programmatically: **53 HTTP endpoints over 50 distinct
  paths** (`/api/missions/{mission_id}/report` is GET *and* POST) plus the WebSocket at `/ws`,
  all present in the document. The doc used `{id}` where the route declares `{mission_id}`;
  corrected. Two routes that existed and were tested but undocumented — `POST /api/system/shutdown`
  and `GET /api/map/manifest` — are now documented.

## 2. Fix-verification experiments (a test that never failed proves nothing)

Each cluster was reverted, the tests re-run, and the code restored. The "restored"
number is what ships.

**2.1 Command lifecycle (CRITICAL C-1…C-4)**
- Reverted → **8 failed, 16 passed**
- Restored → **86 passed** (suite as it stood that day)
- Reverted by removing `SETTLING_TX_STATES`, the endpoint-routing change, the
  `transmitted` flag and the `recall_active` clearance entry.

**2.2 Video subsystem (HIGH H-1…H-3)**
- Reverted → **4 failed, 9 passed in 27.58 s**
  - `test_recording_refuses_a_feed_that_never_delivered_a_frame`
  - `test_a_started_but_silent_feed_is_never_reported_live`
  - `test_a_closed_recording_stamps_its_session_row`
  - `test_a_recorder_that_died_mid_flight_is_closed_as_incomplete`
- Restored → **13 passed in 18.13 s**; `grep -c TEMP-REVERT` = 0 confirmed after restore.
- Reverted by disabling the frame gate in `_recording_blocker`, forcing
  `no_frame_s: None`, and dropping the session-row id so nothing could be closed.

**2.3 Simulation scheduling (the regression this review introduced and caught)**
- Reverted `_schedule_run()` back to `asyncio.create_task(self._run())` →
  **2 failed in 0.26 s**, with `RuntimeError: no running event loop` and
  `RuntimeWarning: coroutine 'SimulationEngine._run' was never awaited`.
- Restored → **2 passed**, full suite **93 passed** at that point.
- The same signature appeared in the end-to-end run before the fix:
  `start with typed START` **FAIL**, `sortie reaches a terminal phase` **FAIL**
  (`phase=EXECUTING`, `delivered=0`, 180 s timeout), and after it the run passes with
  `delivered=10`. That is the honest record: a blocking-endpoint fix broke the flagship
  demo path, the unit suite at the time did not cover it, and the end-to-end run found
  it. `tests/test_threading.py` is the test that now covers it.

**2.4 Offline basemap over-zoom rule (added 2026-11-01)**
- The lookup order in `MapTileService.tile()` was temporarily forced to skip
  `_overzoom_source`, then `test_offline_map.py -k over_zoom` was run →
  **1 failed, 1 passed**: the z17 request returned `\x89PNG…IEND` — a procedurally
  generated tile — where the assertion expected the real OSM parent's bytes.
- Restored → **25 passed in 1.72 s**, and the live server confirmed the same rule over
  HTTP (z17 → 14 997-byte OSM parent).
- This is the experiment that shows the test is not vacuous: without the rule, the
  station silently substitutes invented streets at the zoom where the operator is closest
  to the ground, and nothing on screen changes.

---

## 3. Capability matrix

| # | Capability | Status | Evidence label |
|---|---|---|---|
| 1 | Boot screen verifies backend reachability, offline state, storage, config version | passed | `SIMULATED` |
| 2 | KML boundary import rejects self-intersecting and degenerate polygons | passed | `BUILT` — `test_invalid_polygon_is_rejected` |
| 3 | Lane planning, swath coverage, deterministic plan hash | passed | `CALCULATED` + `BUILT` — `test_plan_lanes_and_swath`, `test_plan_hash_is_deterministic` |
| 4 | Preflight: unknown ≠ pass; blocking checks keep START locked | passed | `BUILT` |
| 5 | Manual sign-offs cannot be auto-passed; SIMULATION batch sign-off is labelled | passed | `BUILT` |
| 6 | Mission upload + per-item read-back diffing (waypoints, fence, params) | passed against the simulator | `SIMULATED`; **not yet physically verified** — 9 read-back tests in `test_upload_readback.py` |
| 7 | Circle fence is never enabled without a radius | passed | `SIMULATED` — `test_fence_circle_is_never_enabled_without_a_radius` |
| 8 | Read-back shorter than the upload is not "verified" | passed | `SIMULATED` — `test_read_back_shorter_than_the_upload_is_not_verified` |
| 9 | A shifted or same-length-but-different mission is not verified | passed | `SIMULATED` — 2 tests |
| 10 | REAL mode refuses to address an aircraft it cannot see | passed | `SIMULATED` — `test_real_mode_refuses_to_address_a_vehicle_it_cannot_see` |
| 11 | Switching to REAL wipes simulated vehicle records | passed | `SIMULATED` |
| 12 | Command lifecycle: SENT → ACKNOWLEDGED → VERIFIED, with `ACK_UNVERIFIED` when the state never arrives | passed | `SIMULATED`; **not yet physically verified** (needs a real autopilot ACK) |
| 13 | ACK from a non-autopilot component is ignored | passed | `SIMULATED` |
| 14 | Unanswered release times out; an untransmitted kit returns to the magazine | passed | `SIMULATED` |
| 15 | A transmitted-but-unproven release is `UNKNOWN`, never recycled | passed | `SIMULATED` |
| 16 | Release only from `HOVER_VERIFY`, only with hover verification | passed | `SIMULATED` |
| 17 | Beam evidence requires an event id and a prior release; idempotent on replay | passed | `BUILT` — `test_beam_evidence_requires_an_event_id`, DB unique index |
| 18 | Allocation: lowest ETA wins, one kit reserved; offline/no-kit aircraft rejected | passed | `SIMULATED` |
| 19 | Survivor registry: capacity, non-finite confidence, absurd uncertainty, merge rules, no geotag tug-of-war | passed | `BUILT` — 8 tests in `test_survivor_registry.py` |
| 20 | Safety: pad interlock, proximity warning, recall blocks release | passed | `SIMULATED` |
| 21 | Abort reconciles kits and answers with a body | passed | `SIMULATED` + e2e run B |
| 22 | Recall clears only the simulator it actually owns | passed | `SIMULATED` — `test_recall_clear_resumes_only_the_simulator` |
| 23 | Video: three feeds, honest statuses, no latency figure without measurement, device contention, recording gates, session close-out | passed | `SIMULATED`/`BUILT` — 13 tests; the analog path is **not executable without hardware** |
| 24 | Station shutdown closes owned services, refuses mid-sortie, requires a token and an operator header, never touches the aircraft | passed | `BUILT` — 4 tests in `test_station_shutdown.py` |
| 25 | Copilot is advisory-only, has no flight or payload path, no cloud dependency | passed | `BUILT` — `test_engine_has_no_llm_dependency`, `test_copilot_is_read_only_and_answers_goal_prompts`, `test_no_endpoint_accepts_raw_mavlink` |
| 26 | HTTP surface: localhost host-header restriction, security headers, offline flag, no glyph server | passed | `BUILT` — `test_api.py` |
| 27 | Frontend typechecks and builds | passed | `BUILT` |
| 28 | Full simulated sortie to completion with 10/10 kits beam-confirmed | passed | `SIMULATED` — e2e run A |
| 29 | SITL: mission protocol against ArduPilot SITL | **not executable without a SITL build in this environment**; procedure documented in `docs/SITL.md` | `PENDING TEST` |
| 30 | RFD868x real-link throughput, latency, multipoint vs independent topology | **not executable without hardware**; `topology_verified` remains false and API-protected | `PENDING TEST` |
| 31 | TX800 analog capture, receiver lock, glass-to-glass latency | **not executable without hardware**; `latency_basis` reports `NOT_MEASURED` | `PENDING TEST` |
| 32 | Servo latch actuation (channel 9 at 1900 µs) and drop mechanics | **not executable without hardware**; `payload_hardware_verified` is false | `PENDING TEST` |
| 33 | Command retry policy (`RETRYING`) | **not implemented** — `attempts` stays 1 | `DESIGNED` only |
| 34 | `SERVO_OUTPUT_RAW` verification of a release command | **not implemented** — deliberate, see `MAVLINK_COMMUNICATION_REVIEW.md` | `DESIGNED` |
| 35 | Transactions persisted across a GCS restart | **not implemented**; consequence is `UNKNOWN`, which is the safe side | `DESIGNED` |
| 36 | Offline basemap: exact tile → covered parent → procedural fallback, never a network call | passed | `SIMULATED` — 25 tests in `test_offline_map.py`, plus the mutation check in §2.4 |
| 37 | Mercator projection and tile-index maths agree; the launch pad renders in the tile that contains it | passed | `SIMULATED` — `test_mercator_corner_and_tile_bounds_agree`, `test_the_launch_pad_is_inside_the_tile_it_renders_in` |
| 38 | Basemap build artifact round-trips: a reloaded geometry rasterises byte-identically | passed | `SIMULATED` — `test_save_and_load_reproduce_the_same_tile_byte_for_byte` |
| 39 | Tile-pack provenance is published, re-read after a rebuild, and degrades to `procedural` when the manifest is corrupt | passed | `SIMULATED` — 4 tests; `GET /api/map/manifest` verified over HTTP |
| 40 | Mission limits on screen come from the same config keys the safety rules consult | passed | `BUILT` — `mission.limits` in `mission_engine.status()`; `tsc --noEmit` clean. No automated UI test exists — see gap G-3 |
| 41 | Map telemetry chip reports fleet `data_quality`, including `STALE`, `LOST`, `MIXED` and `NO TELEMETRY` | passed | `BUILT` — `evidenceQuality()` shared by setup and mission screens; verified by typecheck and reading, not by a browser test (G-3) |
| 42 | Abort and recall stay reachable when blocked, and say why | passed | `BUILT` — `.is-blocked` + toast; same verification caveat as 41 |
| 43 | Confirmation dialogs are named, described, focus-trapped; `Escape` closes only the topmost | passed | `BUILT` — same caveat as 41 |
| 44 | `prefers-reduced-motion` collapses animation without hiding state | passed | `BUILT` — CSS only; not visually regression-tested |

---

## 3a. Coverage gaps, named rather than glossed

- **G-1 — the end-to-end run does not exercise video.** `grep -nE "record|video"
  tools/sim_e2e.py` returns nothing: the script flies, allocates, releases and reconciles,
  but never starts a feed or a recording. The video decisions are covered by 13 unit tests
  against a fake `ffmpeg`, which proves logic, not the pipeline. A sortie-shaped test that
  starts video, records, and stops mid-mission is the most valuable missing test in this
  repository.
- **G-2 — no SITL run.** The MAVLink mission-protocol paths (`upload.py`, read-back
  diffing, fence parameters) have only ever been exercised against pymavlink-shaped message
  doubles and the simulator. `docs/SITL.md` documents the procedure; nobody has run it here.
- **G-3 — the frontend has no automated tests.** Findings 40–44 are `BUILT` and typechecked,
  which proves the code compiles and reads the right fields, not that a screen renders them
  correctly. Rows 40–43 should be read as "implemented and reasoned about", not "tested".
- **G-4 — no crash/power-interruption test.** NFR-002 asks that the ledger survive an
  interruption; WAL mode is configured and the kit lands in `UNKNOWN` on a restart, but
  nothing in this record kills the process mid-release and re-opens the database.

---

## 4. What these results do not establish

- They do not establish anything about flight. There is no airframe in this record.
- They do not establish radio performance. The only links exercised are a simulator and
  a TestClient.
- They do not establish that a real autopilot accepts, executes and reports the commands
  this GCS sends; that is item 29-32 and remains PENDING TEST.
- They do not establish that the mission-readiness gate is sufficient for flight; they
  establish only that it cannot be satisfied by dummy data in this build.
- A "passed" label on a state-machine test means the transition rules behave as
  documented in code, not that the corresponding physical event occurred.
- They do not establish that the basemap is cartographically correct. It is rendered from
  OpenStreetMap as of 2026-10-30 with three documented simplifications (filled polygon
  holes, no labels, bbox culling instead of clipping). It is a reference surface with real
  geometry, not a surveyed chart, and it is labelled as such on screen.
- Rows 40–44 are corrections proven by reading and typechecking. No browser was driven
  against the running station in this session, so the UI fixes are reviewed, not observed.
