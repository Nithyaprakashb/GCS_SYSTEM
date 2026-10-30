# RescueSwarm GCS — Windows Installation

This guide installs the development/runtime station on a Windows 10/11 x64
machine. It assumes no internet access **during the mission**. Package installs
need a connection once; after that the application is fully local.

Verified environment for the recorded test run: Windows, Python 3.14.2,
Node.js 24.11.0, npm 11.6.1, FFmpeg 9.0.2 on `PATH`. The code targets Python
3.11+ and Node 20+.

---

## 1. Prerequisites

| Component | Requirement | Why |
|---|---|---|
| Python | 3.11 or newer | FastAPI backend, pymavlink, Shapely, OpenCV |
| Node.js + npm | Node 20 or newer | build the React UI and Electron shell |
| FFmpeg | on `PATH` | REAL capture/recording; the simulator runs without it |
| Visual C++ runtime | current x64 | OpenCV and native Python wheels |
| Disk | ≥ 2 GB free | evidence folder, recordings, SQLite, map cache |
| Display | 1600×900 minimum | the station is designed for a 1600×980 window |

Optional: three USB serial adapters for independent RFD links, three USB video
capture devices for the TX800 receivers, and an Arduino/FTDI for SITL.

---

## 2. Install the backend

```bat
cd rescueswarm-gcs\backend
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Dependencies: FastAPI, uvicorn, websockets, pydantic, pymavlink, Shapely, NumPy,
Pillow, OpenCV, pytest and httpx. `reportlab` and `PyProj` are not required; the
PDF writer and the local projection are implemented in-tree.

Run the service:

```bat
python -m rescueswarm.main
```

Expected: uvicorn listens on `http://127.0.0.1:8765`. Verify:

```bat
curl http://127.0.0.1:8765/api/health
```

The response must include `"offline": true`. If the port is busy, set
`RESCUESWARM_PORT` to another loopback port for **both** backend and Electron.

Environment variables understood by the backend:

| Variable | Default | Purpose |
|---|---|---|
| `RESCUESWARM_PORT` | `8765` | loopback HTTP port |
| `RESCUESWARM_DATA` | `<repo>\data` | SQLite, evidence, recordings, map cache |
| `RESCUESWARM_FRONTEND` | `<repo>\desktop\dist` | static UI directory |
| `RESCUESWARM_LOG_LEVEL` | `info` | uvicorn log level |
| `RESCUESWARM_PYTHON` | `python` | interpreter used by Electron |

The database and folders are created automatically on first start.

---

## 3. Install the UI and Electron shell

```bat
cd rescueswarm-gcs\desktop
npm install
```

Development UI (browser, hot reload):

```bat
npm run dev
```

Open `http://127.0.0.1:5173`. The Vite dev server proxies API and WebSocket calls
to `127.0.0.1:8765`; see `desktop/vite.config.ts`.

Electron shell in development (loads the Vite server):

```bat
set RESCUESWARM_DEV_URL=http://127.0.0.1:5173
npx electron .
```

Built UI with the shell (production-style):

```bat
npm run build
set ELECTRON_RUN_AS_NODE=
npx electron .
```

`ELECTRON_RUN_AS_NODE=1` makes Electron run as plain Node and fails with
`app.isPackaged` undefined. Clear it before launching. This is common in agent or
CI shells; it is not a code defect.

If the backend is already running, Electron reuses it after checking
`/api/health`. Otherwise it starts `python -m rescueswarm.main` itself with data
in `%APPDATA%\RescueSwarm GCS\data` and logs in
`%APPDATA%\RescueSwarm GCS\logs`.

---

## 4. First start and offline verification

1. Launch the shell. The splash screen lists Python, SQLite, FFmpeg and free
   disk. Missing FFmpeg is reported but does not block simulation.
2. Confirm mode is **SIMULATION** (top bar). REAL is never the default.
3. Work through: Connect fleet → Video init → Import sample KML → Plan →
   Inspect/sign → Preflight → Upload → Readiness gate → type `START`.
4. Confirm the three video panels show labelled simulated feeds and the event log
   records `SIMULATED` quality.
5. Disconnect the network cable/Wi-Fi and repeat. Nothing should fail: map tiles
   are generated locally, fonts are bundled, and no request leaves loopback.

Evidence output lives in `data\` (or `%APPDATA%\RescueSwarm GCS\data` under
Electron): `rescueswarm.db`, `missions\<mission_id>\...`, `video\`, `map_cache\`.

---

## 5. Hardware commissioning (before REAL mode)

Do this only when the physical links exist. None of it is required for
simulation.

1. Enumerate devices with `GET /api/system/diagnostics` and
   `GET /api/system/devices`. A detected port is **not** a verified modem or a
   paired aircraft.
2. Decide the RFD868x topology. Use `independent` with three endpoints unless
   shared multipoint has been proven on the actual units and firmware. Record
   firmware version and region.
3. Set `telemetry.shared_endpoint` or all three
   `telemetry.independent_endpoints`, the correct baud, and
   `topology_verified: true` only after a heartbeat from each system ID (1, 2, 3)
   is observed.
4. Record each capture device's fingerprint and set `video.feeds[].mapped` and
   `device_fingerprint` after visually confirming which receiver shows which
   aircraft.
5. Verify ArduPilot parameters and failsafes on the bench. The GCS writes only
   the documented fence/battery parameters and never disables failsafes.
6. Run the SITL bench first (`docs/SITL.md`), then a prop-off bench test, then a
   tethered test. Mark evidence honestly.

Do **not** set REAL mode with placeholder endpoints; the connect call will fail
and readiness will stay blocked. That is intended.

---

## 6. Running the checks

```bat
cd backend
python -m pytest -q
python scripts\e2e_simulation.py --speed 40
```

```bat
cd desktop
npx tsc -b
npm run build
```

The last recorded results are in `docs/TEST_REPORT.md`.

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Address already in use` | another backend is running; stop it or set `RESCUESWARM_PORT` for both processes |
| Electron shows "backend did not start" | run `python -m rescueswarm.main` from `backend\` and read `%APPDATA%\RescueSwarm GCS\logs\backend.log` |
| `app.isPackaged` undefined | `ELECTRON_RUN_AS_NODE=1`; run `set ELECTRON_RUN_AS_NODE=` first |
| Host 403 | you opened the UI via a non-loopback hostname; use `127.0.0.1` or `localhost` |
| No map tiles | local tile renderer failed; check backend log and `data\map_cache` write permission |
| Video panels black in REAL | capture device not mapped; fill `device_fingerprint`/`mapped`; this does not affect SIMULATION |
| No telemetry in REAL | endpoint wrong, baud mismatch, radio not paired, or wrong system ID; check diagnostics |
| `pymavlink` import error | reinstall requirements in the active virtual environment |
| PDF download 404 | generate the report first (post-mission screen or `POST /api/missions/{id}/report`) |

No step requires an internet connection at runtime except the initial package
installation.
