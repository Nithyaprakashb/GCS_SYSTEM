# RescueSwarm GCS

Offline-first ground control station for **Team ADRODS-X**, NIDAR 2026–27, Problem Statement 1: autonomous aerial search and precision payload delivery.

Three aircraft, one shared kit ledger, deterministic safety, and a complete local evidence trail. No cloud account, no internet map tiles, and no online LLM are required at any point.

> **Evidence labels used throughout:** DESIGNED · CALCULATED · ASSUMED · BUILT · SIMULATED · PHYSICALLY TESTED · PENDING TEST.
> Nothing in this repository is presented as proven flight performance. REAL-mode radio, video capture and mission upload paths are implemented but **PENDING TEST** against hardware.

---

## Status at a glance

| Area | Evidence | Notes |
|---|---|---|
| GCS workflow, planner, allocation, kit ledger, delivery state machine | **BUILT + SIMULATED** | Full 10-survivor mission executed in the deterministic simulator |
| UI (dark/light aerospace design, tactical map, three video panels, replay, logs) | **BUILT + SIMULATED** | Verified in headless Chrome and in the Electron shell |
| Offline basemap | **BUILT** | Procedural raster tiles generated locally; not survey imagery |
| Backend unit/API tests | **BUILT, EXECUTED** | `pytest`: 24 passed; see [TEST_REPORT](docs/TEST_REPORT.md) |
| End-to-end backend simulation | **BUILT, EXECUTED** | `backend/scripts/e2e_simulation.py`: PASSED at 40× |
| Electron Windows shell | **BUILT, SMOKE TESTED** | Launches its own loopback backend and renders the station |
| Windows NSIS package | **BUILT** | Installer produced (90 MB, unsigned); unpacked app launched; clean-machine install **PENDING TEST** |
| REAL RFD868x telemetry topology | **PENDING TEST** | No radio pairing is fabricated from COM-port detection |
| REAL TX800 capture / recording | **PENDING TEST** | Requires explicit receiver-to-aircraft mapping |
| REAL ArduPilot mission/fence upload | **PENDING TEST** | Mission protocol and read-back implemented; not run on hardware |
| Pi 5 + Hailo-8 YOLOv8n integration | **PENDING TEST** | The GCS consumes confirmed survivor reports; it does not paint YOLO boxes on RunCam video |
| Flight / HIL | **PENDING TEST** | No flight test has been performed |

---

## Quick start (development)

A built but unsigned Windows installer is available at
`desktop/release/RescueSwarm-GCS-2.0.0-x64.exe`. The unpacked app was smoke-tested;
clean-machine installation is still pending. See [BUILD_WINDOWS](docs/BUILD_WINDOWS.md)
for prerequisites (Python and FFmpeg are not bundled).

Prerequisites: Windows 10/11, Python 3.11+, Node.js 20+ (verified with Python 3.14.2 and Node 24.11.0), FFmpeg on `PATH` for REAL capture.

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

Full instructions: [INSTALL_WINDOWS](docs/INSTALL_WINDOWS.md) · [BUILD_WINDOWS](docs/BUILD_WINDOWS.md).

---

## Mission workflow

1. Splash and system initialisation — local services, database, safety engine, offline map.
2. Swarm discovery — telemetry topology and per-aircraft identity.
3. Video initialisation — three fixed receiver mappings; health and recording.
4. KML import — boundary validation, AOI, pad clearance.
5. Mission configuration — altitude, speed, swath, lane spacing, time estimate.
6. Hardware inspection — manual sign-offs, all attributed to an operator ID.
7. Automated preflight — 39 checks with measured values, thresholds and evidence source.
8. Upload and read-back — a receipt is only `VERIFIED` when read-back matches.
9. Readiness gate — deterministic launch authority; typed `START` confirmation.
10. Autonomous mission — search, detection, dedupe, allocation, delivery, ACK and break-beam confirmation.
11. Post-mission — reports (JSON/Markdown/PDF), replay, analytics, evidence manifest.

`ABORT` and `EMERGENCY RECALL` are present in the mission header on every mission screen. Both require typed confirmation and are written to the operator-action log with actor identity.

After a mission completes or is aborted, **Mission setup → Start new mission** clears the working session without deleting evidence, so the next sortie gets a new mission ID. Preflight and upload are latched until that reset.

---

## Safety model

- The GCS is an **advisory and command authority only when the operator explicitly acts**. ArduPilot onboard failsafes remain enabled and are never modified by the GCS.
- A failed or killed GCS process cannot disable onboard protections. No endpoint writes failsafe parameters; closing the shell only stops telemetry.
- The safety engine is independent of the AI copilot. The copilot cannot reach MAVLink, the release path, or any safety interlock.
- Release requires: reserved kit, fresh telemetry, armed + GUIDED, TFmini-S height within 3 m ± 1 m, stable hover, inside the inclusion fence, and separation clearance.
- A missing break-beam confirmation produces `UNKNOWN`; it is **never** automatically retried.
- A kit can be reserved for exactly one survivor; a second reservation raises a ledger error.
- All operator actions are recorded with `mission_id`, action, operator, target and reason.

---

## Repository layout

```
backend/
  rescueswarm/       FastAPI service, engines, MAVLink, SQLite, evidence store
  scripts/           e2e_simulation.py
  tests/             pytest unit and API tests
config/fleet.json    Single source of truth for fleet and mission constraints
samples/             KML fixtures
desktop/
  src/               React 19 + TypeScript UI
  electron/          Sandboxed main/preload with loopback-only networking
docs/                Audit, design, API, MAVLink, hardware, install, test, SITL
data/                Created at runtime: SQLite, evidence, map tile cache
```

---

## Documentation

- [Audit, architecture and plan](docs/00_AUDIT_ARCHITECTURE_PLAN.md)
- [Design system](docs/DESIGN_SYSTEM.md)
- [Database schema](docs/DATABASE_SCHEMA.md)
- [HTTP/WebSocket API](docs/API.md)
- [MAVLink mapping](docs/MAVLINK_MAPPING.md)
- [Hardware configuration](docs/HARDWARE_CONFIG.md)
- [Install on Windows](docs/INSTALL_WINDOWS.md)
- [Windows build and packaging](docs/BUILD_WINDOWS.md)
- [Test report](docs/TEST_REPORT.md)
- [SITL and simulation bench](docs/SITL.md)
- [Known issues and limitations](docs/KNOWN_ISSUES.md)

---

## What this GCS deliberately does not do

- It does not invent radio pairing, receiver mappings or vehicle capabilities.
- It does not use internet map tiles or require an online model.
- It does not display YOLO bounding boxes on analogue video without a validated synchronisation pipeline.
- It does not treat simulated receipts, telemetry or deliveries as REAL evidence.
- It does not let the copilot bypass deterministic safety.
- It does not include placeholder buttons that silently do nothing.
