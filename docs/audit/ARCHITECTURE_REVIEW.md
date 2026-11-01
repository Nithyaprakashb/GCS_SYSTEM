# Architecture Review — RescueSwarm GCS

Independent architectural assessment of the station as it exists in this repository.
Reviewer: Qoder, Principal Software Architect pass, 2026-11-01.

This is a review of an existing codebase, not a design proposal for a new one. Every
finding names the file and function it came from. Where a claim could not be checked
without hardware, it is labelled.

**Evidence labels used here**

| Label | Meaning in this document |
|---|---|
| `BUILT` | The code exists and was read; its structure is stated from the source |
| `SIMULATED` | Behaviour was exercised through `backend/tests/` or `tools/sim_e2e.py` |
| `PENDING TEST` | Implemented, but its truth depends on an ArduPilot vehicle, an RFD868x link or a TX800 receiver that was never present |
| `DESIGNED` | Stated by the reference documents in `docs/reference/` and not contradicted here, but not implemented |

Nothing in this document is `PHYSICALLY TESTED`. An architecture that has only ever run
against a simulator is a hypothesis about the wire, not a proven one.

---

## 1. What the system is

A ground control station for a three-aircraft search-and-delivery sortie: one fixed-wing
style searcher (`INS-01`) and two delivery craft, flying a deterministic lawnmower plan
over an imported KML boundary, reporting survivors, and releasing a supply kit against a
geotag. The station is a single-user desktop application. It is not a fleet-management
service, not a cloud product and not a data link: it terminates one MAVLink stream (or
three), one analog video receive path, and one SQLite evidence store on one machine.

Two constraints dominate the architecture and are enforced in code, not in intention:

- **No network.** No online tiles, no cloud LLM, no remote telemetry, no auto-update.
  Loopback only (`api.py` host guard, `main.cjs` origin restriction).
- **The station is not the last line of defence.** Onboard protections must survive a
  dead GCS process. This is respected structurally: the backend is a child process of the
  Electron shell, and nothing in `backend/` writes a parameter that makes the autopilot
  dependent on the station staying alive.

**Sizes, counted at review time** — backend `rescueswarm/`: 29 modules, 10,752 lines.
Frontend `desktop/src/`: 15 TypeScript/TSX modules, 5,325 lines. HTTP surface: 53 endpoints
over 50 distinct paths in one router, plus a WebSocket on a second router at `/ws`. Tests:
118 across 11 files.

---

## 2. Process topology

```
Electron main (desktop/electron/main.cjs)
  ├── spawns  python -m uvicorn rescueswarm.main:app --port 8765      [child process]
  ├── BrowserWindow → http://127.0.0.1:8765  (sandboxed, contextIsolated, no nodeIntegration)
  └── preload.cjs → rescueswarmShell (runtimeInfo only; no fs, no net)

uvicorn process
  ├── FastAPI router (api.py)                      — one event loop
  ├── AppState (state.py)                          — composition root, in-process
  ├── TelemetryManager / mavlink.py                — one reader thread per transport
  ├── VideoManager (video.py)                      — frame-reader threads + ffmpeg subprocesses
  ├── SimulationEngine (simulation.py)             — asyncio tasks bound to a captured loop
  └── SQLite (WAL) + tile cache directory
```

Verified from source: the reader threads (`mavlink.py`), the video health loop
(`video.py` `_health_loop`), ffmpeg child processes, and the SQLite connection all live in
one Python process. There is no second service process and no message broker. The renderer
talks to exactly one origin.

**Assessment.** For a single-operator station this is the right topology. Splitting the
video pipeline into its own process would buy isolation the current design already
achieves with locks and subprocess ownership, at the cost of a second failure domain and
a second thing to keep alive in the field. One judgement worth defending, and it is
defensible.

The one structural risk of the single process is that everything shares one GIL and one
event loop. That risk was real and is now closed: `BUILT` + `SIMULATED` — mission-blocking
work was moved off the loop with `asyncio.to_thread` (`api.py` `start_mission`, `recall`,
`video_stop`), and the regression that a `create_task` on a worker thread has no running
loop is pinned by `tests/test_threading.py` (finding `M-10` in `INDEPENDENT_AUDIT.md`).

---

## 3. Module map and dependency direction

The composition root is `state.py` `AppState`: it constructs the config, database, event
bus, telemetry, video, planner-facing mission engine, safety, ledger, allocation,
delivery, survivors, replay, reporting, copilot and tile service, and exposes them as
attributes. `api.py` holds no logic beyond request validation, the loopback/host guard
and delegation.

Dependency direction, as actually written:

| Layer | Modules | Depends on |
|---|---|---|
| Transport | `mavlink.py`, `video.py` (ffmpeg) | pymavlink, cv2, subprocess |
| Ingest & domain | `telemetry.py`, `mission_engine.py`, `delivery.py`, `ledger.py`, `survivors.py`, `allocation.py`, `safety.py`, `planner.py`, `upload.py`, `kml.py`, `geo.py` | transport, `config.py`, `events.py`, `db.py` |
| Read model | `replay.py`, `reporting.py`, `analytics` in `api.py`, `map_tiles.py`, `system.py` | `db.py`, `events.py` |
| Presentation | `desktop/src/**` | `/api/**` only |

The direction is clean in the sense that matters: **no domain module imports the web
framework**, and no domain module reads the frontend. `preflight.py` and `safety.py` are
pure decision functions over state dictionaries, which is why 118 tests can drive them
without a server. `copilot.py` is imported only by its own route and is gated by
`test_engine_has_no_llm_dependency` (`BUILT`): the mission path cannot reach the LLM.

The one place the layering is nominal rather than real is the event bus: `events.py`
`EventBus.publish` both fans out to live subscribers and writes the durable event table,
so every domain module is implicitly a persistence writer. That is the right call for an
evidence station — see A-5 for the cost.

---

## 4. State machines

Six machines carry the safety-critical behaviour. Each was traced to the transitions its
owner declares, and each was checked against what the frontend displays.

| Machine | Owner | States, as declared in code | Guarded? |
|---|---|---|---|
| Mission phase | `mission_engine.py` | `PLANNED → UPLOADED → READY → EXECUTING → COMPLETED / ABORTED / FAILED` | Yes — phase transitions are single-assignment inside engine methods; `SIMULATED` via `sim_e2e.py` both modes |
| Command transaction | `telemetry.py` | `CREATED → SENT → ACK_PENDING/ACKNOWLEDGED → VERIFYING → COMPLETED / FAILED / TIMEOUT / ABANDONED` | Yes, after fix `C-1`: `ACKNOWLEDGED` was in the sweep's terminal set, so no acknowledged command could ever settle. `TERMINAL_TX_STATES` / `SETTLING_TX_STATES` are now split and the lifecycle cannot re-enter a terminal state |
| Delivery sequence | `delivery.py` `STATES` | `STAGING → TRANSIT → HOVER_VERIFY → RELEASE_SENT → BEAM_CONFIRM → DELIVERED`, plus `ACK_PENDING`, `CONFIRMED`, `UNKNOWN`, `ABORTED` | Yes — release is only reachable from `HOVER_VERIFY`; the frontend spine was rewritten to these names (finding 17) |
| Kit ledger | `ledger.py` | `IN_MAGAZINE → RESERVED → COMMITTED → TRANSMITTED → DELIVERED / UNKNOWN / RETURNED` | Yes — a `TRANSMITTED` frame is never returned to `IN_MAGAZINE` (fix `C-3`), beam evidence cannot mark delivery without a release (fix `14`) |
| Survivor record | `survivors.py` | `DETECTED → ASSIGNED → IN_PROGRESS → DELIVERED / UNSERVED`, with rejection paths | Yes — a delivered survivor cannot be reopened by a late report; a merge cannot move a geotag an aircraft is committed to |
| Video session | `video.py` | `STOPPED → STARTING → (frame) LIVE → DEGRADED → STOPPED`; `video_sessions` DB rows | Yes — a feed with zero frames is never `LIVE` (fix `9`); start and stop share one lifecycle lock (fix `11`) |

The brief's central demand — *do not treat `ACK_ACCEPTED` as proof the physical action
finished* — is implemented as the `VERIFYING` step: an acknowledged command advances only
when the expected vehicle state is read back (`GCS`→`RTL` mode, `NAV_*` waypoint echo,
altitude), and otherwise settles as `ACK_UNVERIFIED`. `SIMULATED` in
`tests/test_command_lifecycle.py`. On the wire this is the difference between the station
knowing a craft turned around and it merely having heard from it; it needs `PENDING TEST`
on real autopilot telemetry to be trusted, but the design refuses to lie either way.

---

## 5. Data flow and the read model

Every state change is published as an event; the event is the audit record, not a side
output. The live path is:

```
domain → EventBus.publish(topic, payload, source, severity, data_quality)
          ├── recent ring (bounded, in memory)   → /api/events, LogsScreen
          ├── durable event table (SQLite)       → /api/missions/{id}/events, replay, report
          └── WebSocket fan-out                  → renderer (snapshot + event frames)
```

The renderer holds one `GcsState` snapshot obtained by polling `/api/state` every 1400 ms
plus a debounced refresh 350 ms after any WS event frame, with a 2.5 s fixed retry on
socket close (`GcsContext.tsx`). Polling is authoritative; malformed frames are ignored
because the poll will correct. For a station that must stay honest when the socket dies,
that is the right ordering, and the fixed retry (no backoff) is acceptable against a
loopback process the same shell supervises.

`data_quality` travels with every event and every vehicle state: `LIVE`, `SIMULATED`,
`STALE`, `LOST`, `UNAVAILABLE`, `MANUAL`, `CALCULATED`, `SYSTEM`. This is the single best
architectural decision in the codebase, and the audit's UI findings were mostly places
where a screen ignored it.

---

## 6. Persistence

SQLite in WAL mode, `foreign_keys=ON`, one connection per process guarded by a lock
(`db.py`). Tables cover missions, events, vehicle state samples, survivors and survivor
observations, kits, allocations, deliveries, video sessions and reports. Migrations are
additive and ad hoc: `PRAGMA table_info(...)` is inspected and missing columns are added.
There is no `user_version` and no ordered migration ladder.

Deliberately **not** persisted: in-flight command transactions (`telemetry.py` keeps a
dict, evicted past 500). The consequence is stated honestly in the audit (`M-3`): after a
GCS restart an ACK that arrives late cannot settle the release, and the kit becomes
`UNKNOWN` — the safe outcome, at the cost of the audit trail's resolution. This is
recorded as a known, chosen trade, not a gap nobody noticed.

---

## 7. Configuration as the single source of truth

`config/fleet.json` is the only place aircraft geometry, link endpoints, servo channels,
altitudes, speeds, separation limits and caps are declared; `config.py` validates, clamps
and protects. Protected keys — safety thresholds, `topology_verified`,
`payload_hardware_verified`, `copilot.advisory_only` — are refused through the patch
route, with tests (`BUILT`). Every endpoint in the shipped profile is `null`, which is why
REAL mode cannot be entered without commissioning.

One architectural defect in this area was found and fixed this cycle (`BUILT`): the setup
screen displayed search altitude, speed, survivor cap, merge radius and release clearance
as literals, while `safety.py` and `survivors.py` read them from the profile. The station
could therefore advertise a clearance floor it was not enforcing. `mission_engine.status()`
now publishes the same keys under `mission.limits`, and the UI reads them, rendering `—`
when absent. The rule that came out of it is worth stating for any future screen:
**a limit shown to the operator must come from the same config key the rule consults.**

---

## 8. The offline map, as an architectural element

`map_tiles.py` `MapTileService` serves a raster style with two possible surfaces and a
machine-readable statement of which one is on screen:

- an OpenStreetMap tile pack on disk (`data/map_tiles/{z}/{x}/{y}[-light].png` plus
  `map_manifest.json`), built by `tools/offline_map.py` from a Geofabrik `.pbf` through
  pyrosm, rendered by `tools/osm_basemap.py`; 469,124 features → 1,572 tiles, z10–z16,
  both themes;
- a procedural terrain field (`_render`), deterministic and generated locally, for anywhere
  the pack does not cover.

Lookup order is exact tile → over-zoom parent → procedural. The style's source `maxzoom`
is set from the manifest so MapLibre over-zooms a real tile instead of requesting a zoom
the pack never rendered, and the tile endpoint repeats that rule for direct hits (`/api/map/manifest`
publishes source, data date, coverage, simplifications and `network_access: false`).
`SIMULATED` — `backend/tests/test_offline_map.py`, 25 tests, including a mutation check:
with the over-zoom lookup removed, the z17 request returned an invented PNG.

This is the correct shape for an offline basemap: the surface is data, the provenance is a
first-class API response, and the fallback is explicitly labelled as a fallback.

---

## 9. Findings

Format: file / component | problem | severity | evidence | recommended correction.

**A-1 — `mission_engine.py` is a composition root as well as a domain object**
`backend/rescueswarm/mission_engine.py` (789 lines) | The engine owns the mission phase
machine and directly orchestrates planner, safety, allocation, ledger, delivery, survivors,
upload and telemetry. Every test that needs an engine needs a full `AppState`. The
orchestration is readable, but the class has no seam: a second mission type (e.g. a
pure-search sortie with no delivery) must be added inside the same state machine. |
MEDIUM | `BUILT` | Keep the class — do not attempt a decomposition rewrite. Introduce one
narrow seam: make the delivery pipeline a collaborator the engine is handed (it already
receives `SafetyMonitor`, `KitLedger` etc. via `AppState`; a `MissionProfile` object
declaring which phases exist would let the state machine be data rather than code). Add it
when the second sortie type is actually requested, not before.

**A-2 — no GCS heartbeat: the station is a MAVLink client the autopilot cannot see**
`backend/rescueswarm/mavlink.py`, `telemetry.py` | There is no `HEARTBEAT` transmission
anywhere in `backend/` (grep of `HEARTBEAT` with a send/pack/maker: no matches). The
autopilot's `FS_GCS_ENABLE` comm-loss behaviour therefore has nothing to observe, and
`SYSID_MYGCS` is never written. Two architectural consequences: (a) the "onboard failsafe"
layer the reference design promises is not actually armed against GCS loss; (b) a
half-dead station — process alive, link down — is indistinguishable from a healthy one to
the aircraft. | **HIGH** | `BUILT` (absence is a source fact); the behaviour is
`PENDING TEST` | Send a GCS `HEARTBEAT` with a dedicated `sysid` at a fixed rate on each
live transport, before `FS_GCS_ENABLE` is ever written. This is a prerequisite for H-6, not
a follow-up to it. Do not arm comm-loss failsafe first — that inverts the safety order.

**A-3 — the command journal is memory-only**
`telemetry.py` transactions dict, `db.py` | Durable events record `command.*` publishes,
but the transaction itself (its expectations, attempts and terminal reason) lives in a
bounded in-process dict. A restart mid-release loses the explanation for a kit in
`UNKNOWN`, which is exactly the record an investigator needs. | MEDIUM | `BUILT`;
`SIMULATED` that the kit lands in `UNKNOWN` rather than being recycled (`C-3` test) |
Persist transactions in a small `command_transactions` table written at SENT and at
settlement, and replay un-settled rows at startup into `UNKNOWN` with the reason recorded.
This is an additive table; no state machine changes.

**A-4 — one router, 53 endpoints, no versioning**
`backend/rescueswarm/api.py` (684 lines) | All handlers live in a single module under a
single `/api` prefix, while the WebSocket sits on a second router at `/ws` with no prefix at
all — an inconsistency a new client has to know about rather than discover. Nothing consumes
the API but this repository's own renderer, so the absence of versioning costs little today;
the size costs more — the file mixes transport concerns (host guard, `to_thread` offloading,
`Response` construction) with 50 distinct paths. |
LOW today, MEDIUM as it grows | `BUILT` | Split by domain into `api_mission.py`,
`api_video.py`, `api_map.py`, `api_records.py` behind the same router aggregation, and
move the loopback/host guard into a middleware module. Pure relocation: no handler body
changes, which is what makes it safe to do opportunistically rather than as a project.

**A-5 — the live log is a bounded ring, and the screen that shows it is the ring**
`events.py` `recent`, `LogsScreen.tsx` | The in-memory ring is capped, so the log screen
silently truncates history that the database still holds at
`/api/missions/{id}/events`. An operator reading "the last N events" on screen can mistake
the window for the record. | MEDIUM | `BUILT`; recorded in the audit as `M-6` | Add a
"load earlier" path on the log screen against the durable endpoint. The ring is the right
structure for live fan-out; only the presentation is wrong.

**A-6 — tile provenance is per-request, but coverage is per-region**
`map_tiles.py` `provenance()`, `style()`, `TacticalMap.tsx` | The manifest publishes the
covered boxes and the style declares the source, but nothing on the map distinguishes a
real street from procedural terrain *inside* the same view when the mission straddles the
edge of coverage — the operator sees one surface and one label. The tile pack covers
Bengaluru at z≤12 and the demo box at z13–16; a mission flown outside them renders
invented terrain with an honest but global label. | MEDIUM | `SIMULATED` (the tests prove
the fallback fires and returns a valid PNG for an uncovered tile) | Draw the coverage
rectangles from `/api/map/manifest` as a distinct "no survey data here" overlay, or stamp
procedural tiles with a corner mark. Cheap, and it converts a global disclaimer into a
local fact the operator can act on.

**A-7 — screen components are the largest units in the frontend**
`SetupScreen.tsx` (1,138 lines, six stage components in one file), `MissionScreen.tsx`
(678) | The stage components are properly separated as functions with explicit props, and
this cycle moved shared derivations (`evidenceQuality`, `statusTone`) into `lib/format.ts`
so screens cannot disagree — `BUILT`. What remains is file size and prop drilling through
five levels of the setup wizard. | LOW | `BUILT` | Extract the per-stage panels when a
stage next changes; do not restructure proactively. Prop drilling at this depth is
unpleasant but honest — there is no hidden store to reason about.

**A-8 — schema migration has no version marker**
`db.py` | Additive `PRAGMA table_info` checks work and are idempotent, but there is no
`user_version`, so an upgrade cannot tell whether a migration already ran, and a future
non-additive change (a renamed column, a re-typed value) has no place to be sequenced. |
LOW | `BUILT` | Set and read `PRAGMA user_version`, and keep the existing column checks as
version-gated steps. One small function, before the schema grows again.

**A-9 — video and telemetry share a process but not a failure domain**
`video.py`, `telemetry.py` | Stated here because it is the question an architecture review
has to answer: can a stuck ffmpeg pipe stop the telemetry path? Frame reads and health live
on their own threads with their own lock, and the HTTP handlers that could block were moved
off the event loop (finding 8). The remaining coupling is the GIL: a CPU-bound cv2 decode
loop can delay the reader thread's parsing. | MEDIUM, unquantified | `PENDING TEST` —
measuring it needs real TX800 feeds at three simultaneous decode rates; the simulator's
synthetic frames do not exercise the decoder | Record it as a bench measurement to perform
(reader-thread latency with three live feeds), not as a defect to fix in code. If it
measures badly, the fix is process isolation for the video pipeline, and the topology
section above already sketches where that seam would sit.

---

## 10. What the architecture gets right

Worth naming, because these are the decisions a future maintainer must not casually erode.

The evidence label is carried in the data, not in the prose. `data_quality` is a field on
events and vehicle states, `evidence` is a field on plan artifacts, and the tile manifest
is an API response. A screen can misrender them — two did, and are fixed — but the station
never had to be told whether a number was measured or invented; it already knew.

Refusal is implemented at the boundaries where it matters: REAL mode will not address an
aircraft the station cannot see, safety thresholds will not accept a config patch, a
zero-frame feed will not be called live, an acknowledged command will not be called
completed, a `TRANSMITTED` frame will not go back on the shelf, and a tile above the pack
will not be fabricated when a real parent exists. Each of those is a line of code with a
test attached, and each is the difference between a station that is optimistic and a
station that is wrong.

The frontend has no business logic that could contradict the backend — it renders
snapshots. The exceptions were exactly the defects this review found (the map's provenance
chip derived from `mission.mode`; the setup screen's hard-coded limits), and both were
fixed by moving the derivation to the side that owns the data.

---

## 11. Verdict

The architecture is sound for the scope it serves, and its weakest points are known and
documented rather than hidden. Nothing here requires a rewrite; A-2 (GCS heartbeat) is the
only finding that changes what the station is allowed to claim about onboard protection,
and it is a bounded piece of work in two files. A-3 and A-6 are the two that most improve
what an operator can trust on screen.

**Structural readiness: 8/10.** Docked one point for the missing GCS heartbeat, which makes
a documented safety layer aspirational, and one for the transaction journal, which leaves
the most important investigation — what was commanded, and what settled it — partly in
memory. Both are fixable without touching a state machine.

**Not flight-ready.** No ArduPilot vehicle, RFD868x modem or TX800 receiver has ever been
connected to this station. Every MAVLink statement in this codebase is a statement about
what the code does with a message object. The readiness gate, the protected keys, the
`null` endpoints in the shipped profile and the refusal to address an unseen aircraft are
what keep that fact in front of the operator.
