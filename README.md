# RescueSwarm GCS

Offline-first ground control station for **Team ADRODS-X**, NIDAR 2026–27, Problem Statement 1: autonomous aerial search and precision payload delivery.

Three aircraft, one shared kit ledger, deterministic safety, and a complete local evidence trail. No cloud account, no internet map tiles, and no online LLM are required at any point.

> **Evidence labels used throughout:** DESIGNED · CALCULATED · ASSUMED · BUILT · SIMULATED · PHYSICALLY TESTED · PENDING TEST.
> Nothing in this repository is presented as proven flight performance. REAL-mode radio, video capture and mission upload paths are implemented but **PENDING TEST** against hardware.
> This station has never flown an aircraft. "Tests pass" means the state machines behave as documented in code.

---

## Status at a glance

Measured on 2026-11-01 by running the commands printed next to each row; the full record is
[docs/audit/TEST_RESULTS.md](docs/audit/TEST_RESULTS.md).

| Area | Evidence | Notes |
|---|---|---|
| GCS workflow, planner, allocation, kit ledger, delivery state machine | **BUILT + SIMULATED** | Full 10-survivor sortie executed in the deterministic simulator, 16/16 checks, `phase=COMPLETE`, `delivered=10` |
| Abort and recall reconciliation | **BUILT + SIMULATED** | Abort run 18/18 checks green, `phase=ABORTED`, `counts={"LOADED": 10}`, `returned=1 unverified=0` |
| Command lifecycle | **BUILT + SIMULATED** | `CREATED → SENT → ACKNOWLEDGED → VERIFIED`, with `ACK_UNVERIFIED` when the vehicle state never arrives; an ACK from a non-autopilot component is ignored |
| Backend tests | **BUILT, EXECUTED** | `python -m pytest -q` → **118 passed in 29.36 s** (29 backend modules, 10,752 lines) |
| Frontend | **BUILT, EXECUTED** | `npx tsc --noEmit` clean; `npx vite build` succeeds. No UI automation, so screen behaviour is reviewed, not observed |
| Offline basemap | **BUILT, EXECUTED** | 1,572 raster tiles rendered locally from a Geofabrik OSM extract; procedural fallback is clearly labelled as invented terrain |
| Windows NSIS package | **BUILT** | Installer produced (90 MB, unsigned); unpacked app launched; clean-machine install **PENDING TEST** |
| REAL RFD868x telemetry topology | **PENDING TEST** | `topology_verified` is false and API-protected. No radio pairing is fabricated from COM-port detection |
| REAL TX800 capture / recording | **PENDING TEST** | Requires explicit receiver-to-aircraft mapping; glass-to-glass latency is reported `NOT_MEASURED` |
| REAL ArduPilot mission/fence upload | **PENDING TEST** | Mission protocol and per-item read-back diffing implemented; exercised against the simulator only |
| Servo latch / break-beam actuation | **PENDING TEST** | `payload_hardware_verified` is false |
| GCS `HEARTBEAT` transmission | **NOT IMPLEMENTED** | The station never transmits a heartbeat, so ArduPilot GCS failsafe must stay disabled. See [traceability FR-016](docs/audit/REQUIREMENTS_TRACEABILITY.md) |
| Command retries (`RETRYING`) | **NOT IMPLEMENTED** | `attempts` stays 1; a lost recall packet is not re-sent. Deliberate, and recorded as an open defect |
| Pi 5 + Hailo-8 YOLOv8n integration | **PENDING TEST / separate repository** | The GCS consumes confirmed survivor reports; it does not paint YOLO boxes on RunCam video |
| Flight / HIL | **PENDING TEST** | No flight test has been performed |

---

## Quick start (development)

A built but unsigned Windows installer is available at
`desktop/release/RescueSwarm-GCS-2.0.0-x64.exe`. The unpacked app was smoke-tested;
clean-machine installation is still pending. See [BUILD_WINDOWS](docs/BUILD_WINDOWS.md)
for prerequisites (Python and FFmpeg are not bundled).

Prerequisites: Windows 10/11, Python 3.11+ (verified on 3.14.2), Node.js 20+ (verified on 24.11.0), FFmpeg on `PATH` for REAL capture.

```bat
cd backend
python -m pip install -r requirements.txt
python -m rescueswarm.main
```

In a second terminal:

```bat
cd desktop
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The backend listens only on `127.0.0.1:8765`.

For the packaged-style shell:

```bat
cd desktop
npm run build
set ELECTRON_RUN_AS_NODE=
npx electron .
```

> If `ELECTRON_RUN_AS_NODE=1` is present in your shell (common in agent/CI environments), clear it before launching Electron, otherwise Electron's main process is executed as plain Node.

Verification, from the repository root:

```bat
cd backend && python -m pytest -q
python tools/sim_e2e.py --timeout 420
python tools/sim_e2e.py --abort-after 12
cd desktop && npx tsc --noEmit && npx vite build
```

Full instructions: [INSTALL_WINDOWS](docs/INSTALL_WINDOWS.md) · [BUILD_WINDOWS](docs/BUILD_WINDOWS.md).

---

## The offline map

The station never fetches a tile from the internet. It serves local raster tiles from
`/api/map/tiles/{z}/{x}/{y}.png`, and the surface under the mission geometry is one of two things,
each labelled:

1. **An OSM tile pack** built locally from a Geofabrik extract — real streets, lakes, boundaries.
   `GET /api/map/manifest` reports `kind: "openstreetmap-offline-pack"` with the data date.
2. **A procedural fallback** — deterministic coloured noise. Useful as a reference surface, useless
   as terrain, and therefore reported as `kind: "procedural"` and attributed
   *"Procedural offline reference · not satellite imagery"* in the map style itself.

Build the pack once, from the repository root. First place a regional extract where the tool
looks for it — the download is the operator's step, not the station's, and there is no URL
fetched by any code in this repository:

```bat
:: 1. put a Geofabrik regional extract at map\india-261030.osm.pbf
python tools/offline_map.py extract
python tools/offline_map.py build --zooms 10-16
python tools/offline_map.py status
```

`extract` reads the `.pbf` with pyrosm and caches the geometry as parquet under `map/extract/`.
`build` renders z10–z16 in both themes into the served tile directory (`data/map_tiles/`) and
writes `data/map_tiles/map_manifest.json`, which records the source file, OSM data date, feature
counts, coverage boxes, zoom range and the simplifications applied (interior rings filled rather
than punched, no street or place labels, geometry culled by bounding box rather than clipped to
tile edges). `status` prints that manifest. Intermediate basemap geometry lives in `map/build/`.

The `.pbf` (~1.6 GB), `map/extract/` and the generated tiles are git-ignored; the pack is
reproducible from the commands above.

Provenance is served, not asserted — `GET /api/map/manifest` reports `network_access: false`, the
data date, and whether a pack exists. The style's `maxzoom` stops at the pack top, so zooming past
it over-zooms a **real** tile instead of asking the server for a tile it would have to invent.
Tested in `backend/tests/test_offline_map.py` (25 tests).

---

## Mission workflow

1. Splash and system initialisation — local services, database, safety engine, offline map.
2. Swarm discovery — telemetry topology and per-aircraft identity.
3. Video initialisation — three fixed receiver mappings; health and recording.
4. KML import — boundary validation, AOI, pad clearance.
5. Mission configuration — altitude, speed, swath, lane spacing, time estimate; every figure shown comes from the same config key the safety rule consults.
6. Hardware inspection — ten manual sign-offs, each attributed to an operator, none auto-passable.
7. Automated preflight — role-specific checks with measured values, thresholds and evidence source. Unknown is not pass.
8. Upload and read-back — a receipt is only `VERIFIED` when read-back matches, item by item.
9. Readiness gate — deterministic launch authority; typed `START` confirmation.
10. Autonomous mission — search, detection, dedupe, allocation, delivery, ACK and break-beam confirmation.
11. Post-mission — reports (JSON/Markdown/CSV/PDF), replay, analytics, evidence manifest.

`ABORT` and `EMERGENCY RECALL` are present in the mission header on every mission screen. Both require typed confirmation and are written to the operator-action log with actor identity. When a precondition blocks one, the button stays focusable and explains the blocking condition instead of silently doing nothing.

After a mission completes or is aborted, **Mission setup → Start new mission** clears the working session without deleting evidence, so the next sortie gets a new mission ID. Preflight and upload are latched until that reset.

---

## Safety model

- The GCS is an **advisory and command authority only when the operator explicitly acts**. ArduPilot onboard failsafes remain enabled and are never modified by the GCS.
- A failed or killed GCS process cannot disable onboard protections. No endpoint writes failsafe parameters; closing the shell only stops telemetry.
- The safety engine is independent of the AI copilot. The copilot cannot reach MAVLink, the release path, or any safety interlock — enforced by `test_engine_has_no_llm_dependency` and `test_no_endpoint_accepts_raw_mavlink`.
- Release requires: reserved kit, fresh telemetry, armed + GUIDED, TFmini-S height within tolerance, verified hover, inside the inclusion fence, and separation clearance. A recall in progress blocks release.
- A missing break-beam confirmation produces `UNKNOWN`; it is **never** recycled into the magazine and never auto-retried.
- A kit can be reserved for exactly one survivor; a second reservation raises a ledger error.
- Beam evidence requires an event id and a prior release event, and is idempotent on replay through a unique index.
- Every displayed number carries provenance — `LIVE`, `SIMULATED`, `STALE`, `LOST`, `UNAVAILABLE`, `MANUAL`, `CALCULATED` or `RECORDED` — including the map chip, which distinguishes a lost link from a simulation.
- A station shutdown requires a token and an operator header, refuses while a sortie is executing, closes the services it owns, and never touches the aircraft.

---

## Repository layout

```
backend/
  rescueswarm/       FastAPI service, engines, MAVLink, SQLite, evidence store
  scripts/           e2e_simulation.py (legacy harness)
  tests/             pytest unit and API tests (118 tests, 11 files)
config/fleet.json    Single source of truth for fleet and mission constraints
samples/             KML fixtures
desktop/
  src/               React 19 + TypeScript UI (5,325 lines)
  electron/          Sandboxed main/preload with loopback-only networking
docs/                API, MAVLink, hardware, install, test, SITL
docs/audit/          Independent review: audit, UI/UX, architecture, MAVLink,
                     video, safety, test results, prioritised fixes, traceability
tools/               sim_e2e.py, offline_map.py, osm_basemap.py
map/                 OSM extract and generated tile pack (git-ignored)
data/                Created at runtime: SQLite, evidence, served tile cache
```

---

## Documentation

Independent review (this cycle):

- [Independent audit](docs/audit/INDEPENDENT_AUDIT.md)
- [UI/UX review, per screen](docs/audit/UI_UX_REVIEW.md)
- [Architecture review](docs/audit/ARCHITECTURE_REVIEW.md)
- [MAVLink communication review](docs/audit/MAVLINK_COMMUNICATION_REVIEW.md)
- [Video system review](docs/audit/VIDEO_SYSTEM_REVIEW.md)
- [Safety and failure analysis](docs/audit/SAFETY_AND_FAILURE_ANALYSIS.md)
- [Test results, as executed](docs/audit/TEST_RESULTS.md)
- [Prioritised fixes](docs/audit/PRIORITISED_FIXES.md)
- [PRD/TRD requirements traceability](docs/audit/REQUIREMENTS_TRACEABILITY.md)
- [Changelog](docs/CHANGELOG.md)

Product and integration:

- [Audit, architecture and plan](docs/00_AUDIT_ARCHITECTURE_PLAN.md)
- [Design system](docs/DESIGN_SYSTEM.md)
- [Database schema](docs/DATABASE_SCHEMA.md)
- [HTTP/WebSocket API](docs/API.md)
- [MAVLink mapping](docs/MAVLINK_MAPPING.md)
- [Hardware configuration](docs/HARDWARE_CONFIG.md)
- [Install on Windows](docs/INSTALL_WINDOWS.md)
- [Windows build and packaging](docs/BUILD_WINDOWS.md)
- [Test report (earlier cycle)](docs/TEST_REPORT.md) · [Current test results](docs/audit/TEST_RESULTS.md)
- [SITL and simulation bench](docs/SITL.md)
- [Known issues and limitations](docs/KNOWN_ISSUES.md)
- [Reference PRD/TRD/UI-UX report](docs/reference/RescueSwarm_GCS_Master_PRD_TRD_UIUX_Architecture_Report.md)

---

## What this GCS deliberately does not do

- It does not invent radio pairing, receiver mappings or vehicle capabilities.
- It does not use internet map tiles or require an online model.
- It does not display YOLO bounding boxes on analogue video without a validated synchronisation pipeline.
- It does not treat simulated receipts, telemetry or deliveries as REAL evidence, and does not label a stale or lost link as a simulation.
- It does not let the copilot bypass deterministic safety.
- It does not include placeholder buttons that silently do nothing.
- It does not claim a mission-protocol upload was verified from a send-only status.
- It does not quote a latency, throughput or flight figure it has not measured.
