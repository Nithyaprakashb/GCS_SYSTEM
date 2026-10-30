# RescueSwarm GCS — Test Report

Run date: **2026-10-30**. Host: Windows, x64. This report records only tests
that were actually executed in this workspace. Hardware tests were not executed
and are marked **PENDING TEST**.

Evidence labels: FAIL means the check failed; PASS means it ran and passed. A
simulated mission is never described as flight evidence.

---

## 1. Environment

| Component | Version |
|---|---|
| Python | 3.14.2 |
| FastAPI / uvicorn | 0.128.2 / 0.40.0 |
| pymavlink | 2.4.50 |
| SQLite | 3.50.4 |
| Shapely / NumPy / Pillow / OpenCV | 2.2.0 / 2.4.2 / 12.2.0 / 5.0.0.93 |
| pytest / httpx / websockets | 9.1.1 / 0.28.1 / 17.3 |
| Node.js / npm | 24.11.0 / 11.6.1 |
| React / Vite / TypeScript | 19.2 / 7.3.7 / 5.9.3 |
| MapLibre GL JS | 5.9 |
| Electron | 33.4.11 (Chrome 130) |
| FFmpeg | 9.0.2 |

---

## 2. Automated backend tests — PASS

Command:

```bat
cd backend
python -m pytest -q
```

Result: **24 passed in 1.10 s**.

Coverage by file:

| File | What it proves |
|---|---|
| `tests/test_kml_planner.py` | 9.97 ha sample parsed; invalid polygon rejected; 9 lanes; 22.24 m spacing; 3 geofences; deterministic plan hash; positive time estimate |
| `tests/test_safety.py` | parked aircraft at the same pad do not raise proximity; airborne proximity does; release requires stable hover; pad interlock; safety engine has no network client |
| `tests/test_allocation_delivery.py` | lowest ETA wins; exactly one kit reserved; no free kit rejects the second delivery aircraft; offline aircraft rejected; `DELIVERED` badge survives climb/return/park; illegal transition rejected; double release blocked by ledger; second reservation raises |
| `tests/test_api.py` | health reports `offline: true`; Host guard returns 403; CSP and security headers present; map style uses raster tiles and no glyphs; state sections present; reset clears the session; copilot answers three read-only prompts; no raw-MAVLink endpoint exists |

These are unit/API tests with a temporary data directory. They do not touch
hardware.

---

## 3. End-to-end backend simulation — PASS

Command:

```bat
cd backend
python scripts/e2e_simulation.py --speed 40
```

Result: **E2E PASSED**.

```
mission_id: MSN-20261030-133518-EF2A
phase: COMPLETE
counts: {'DELIVERED': 10}
kits: {'DELIVERED': 10}
samples: 114
plan: {'lanes': 9, 'path_m': 4606.4}
sim_time: 798.1 s
```

Verified assertions:

- Three simulated aircraft connected with labelled simulated identities.
- KML imported and planned: 9 lanes, geometric validation, 3 geofences.
- Preflight blocked before upload; upload was the blocking check.
- Upload returned `VERIFIED` for all three aircraft, each marked `simulated`.
- Readiness gate opened; mission entered `EXECUTING`.
- Mission completed as `SIMULATION_COMPLETE`; all ten planted survivors
  delivered; no survivor left `UNSERVED`; every survivor resolved as `DELIVERED`
  or `UNKNOWN`.
- Kit ledger matched delivered and unknown counts.
- Every one of the ten allocations carried candidate decision evidence.
- Evidence written: `manifest.json`, `plan/plan.json`,
  `plan/upload_receipts.json`, `survivors/allocations.jsonl`,
  `reports/report.{json,md,pdf}`, `reports/completion_summary.json`.
- PDF open/close markers valid.
- Recordings exist for INS-01, DEL-01 and DEL-02.
- Replay has a telemetry track, AGL/quality samples and a populated timeline.
- Analytics survivor total is correct.

The simulator ran at 8× in the UI and 40× in this script. All of it is
**SIMULATED**.

---

## 4. Frontend type-check and production build — PASS

```bat
cd desktop
npx tsc -b
npm run build
```

Result: TypeScript clean; Vite transformed 1 598 modules; build finished in
15.55 s.

Output: `dist/assets/index-DVzrZ_jr.js` 1 409.01 kB (392.41 kB gzip),
`index-DMj3iM5A.css` 126.92 kB. Vite warns that the main chunk exceeds 500 kB
(MapLibre GL). This is a size warning, not a failure, and is listed in
`docs/KNOWN_ISSUES.md`.

---

## 5. UI workflow — PASS (simulated)

Executed in headless Chrome via CDP against Vite (`127.0.0.1:5173`) and the local
backend, then repeated in the Electron shell.

| Step | Observed |
|---|---|
| Splash / init | backend version, Python, SQLite, FFmpeg, free disk shown |
| Fleet discovery | 3/3 aircraft connected in SIMULATION |
| Video init | 3/3 feeds healthy; panel labels identify simulated feeds |
| Sample KML import | 10.0 ha, pad clearance valid |
| Plan | 9 lanes, 4.61 km path, ~10.0 min estimate |
| Inspection / sign-off | all manual items signed; recorded with actor |
| Preflight | 36–39 checks passing depending on stage; blocking upload check visible |
| Upload | all receipts `VERIFIED`, explicitly simulated |
| Gate | open only after upload + signatures; START required typed token |
| Mission | autonomous search, detection, dedupe, allocation, delivery |
| Completion | 10/10 delivered; allocations show `DELIVERED`; tasks parked `STANDBY` |
| Recordings | INS-01 4.6 MB, DEL-01 3.1 MB, DEL-02 3.4 MB |
| Reset | completed session cleared to SETUP; kits/allocations cleared; evidence retained |
| Abort | typed confirmation; mission `ABORTED`; all aircraft LOITER |
| Emergency recall | typed confirmation; recall banner while EXECUTING; all aircraft land and disarm; outcome `EMERGENCY_RECALL_COMPLETE` |
| Timer | shows simulated elapsed time against the 30:00 cap (e.g. 11:10), not wall time |
| Map | offline procedural tiles; boundary, lanes, fleet, survivors rendered |

Bugs found and fixed during this verification:

1. Allocation badges showed internal `STANDBY` after delivery. Fixed by mapping
   delivery states to operator status and updating only mapped states; verified
   `DELIVERED`.
2. Timer showed wall time beside a simulated 30-minute cap. Fixed to show
   simulated elapsed time with a "Simulated elapsed" label.
3. Copilot chips did not answer their own questions; handlers and context added
   and keyword conflicts removed. Verified all three prompts.
4. False proximity warnings for three parked aircraft at one pad point. Fixed by
   skipping pairs where neither aircraft is airborne; verified no warnings.
5. No way to start a new mission after completion/abort; preflight could revive
   a finalised session with consumed kits. Added reset route, phase guards and
   UI action; verified reset to `SETUP` with cleared kits.
6. CSP was absent from HTTP responses. Added a loopback-only CSP with no inline
   scripts; verified header presence in tests.

---

## 6. Electron shell — PASS (smoke)

Launch:

```bat
cd desktop
env -u ELECTRON_RUN_AS_NODE npx electron . --remote-debugging-port=9333
```

Observed via CDP: single window, splash screen listing Python 3.14.2, SQLite
3.50.4, FFmpeg path and 53 172 MB free, then the station UI. The shell reused or
started its own loopback backend. Context isolation, sandbox, disabled Node
integration, denied permissions, blocked external requests and single-instance
lock are configured in `electron/main.cjs`.

Not tested: killing the backend mid-mission in the packaged shell, renderer
crash recovery, and installer behaviour (see below).

---

## 7. Windows NSIS package — BUILT; clean-machine install PENDING TEST

Command:

```bat
cd desktop
npm run dist:win
```

Result: **build succeeded.** Produced:

- `release\RescueSwarm-GCS-2.0.0-x64.exe` — 90 MB, unsigned NSIS installer;
- `release\win-unpacked\` — 355 MB unpacked app.

The unpacked application was launched with remote debugging. It started its own
backend from `resources\backend`, served the UI from `resources\desktop\dist`,
reported `offline: true`, rendered the splash screen (Python 3.14.2, SQLite
3.50.4, FFmpeg, 52 308 MB free) and entered Mission setup with `SETUP`,
`SIMULATION`, 0/3 links and 0/10 kits.

Not tested: running the `.exe` installer, installing to a clean machine,
uninstall, absence of Python on the target, and code signing. The installer is
unsigned. These remain **PENDING TEST**; no clean-machine claim is made.

---

## 8. Hardware and HIL — NOT RUN (PENDING TEST)

| Area | Status | Notes |
|---|---|---|
| RFD868x shared multipoint | PENDING TEST | topology unverified; no radio addressed |
| RFD868x independent P2P | PENDING TEST | code path exists |
| ArduPilot mission/fence/parameter upload | PENDING TEST | protocol implemented; no hardware read-back |
| ARM / takeoff / RTL / land commands | PENDING TEST | only simulator ACKs observed |
| TFmini-S AGL and 3 m release clearance | PENDING TEST | simulated values only |
| MG90S servo release | PENDING TEST | no servo actuated |
| IR break-beam confirmation | PENDING TEST | simulated beam only |
| TX800 capture and three-device mapping | PENDING TEST | fingerprints and `mapped` are null/false |
| Pi 5 + Hailo-8 YOLOv8n survivor reports | PENDING TEST | GCS-side validation tested with synthetic envelopes |
| ArduPilot SITL | DESIGNED | instructions written; not executed here |
| Flight test | NOT PERFORMED | no flight claimed |

---

## 9. Result summary

| Check | Result |
|---|---|
| Backend unit/API tests | PASS — 24/24 |
| Backend end-to-end simulation 40× | PASS |
| TypeScript type-check | PASS |
| Vite production build | PASS (chunk-size warning) |
| UI workflow in headless Chrome | PASS (SIMULATED) |
| Electron shell smoke test | PASS |
| Windows NSIS installer | BUILT — unpacked app smoke-tested; clean-machine install PENDING TEST |
| Any hardware/HIL/flight test | PENDING TEST / NOT PERFORMED |

The software is credible as a **simulation-verified, hardware-pending** GCS. It
must not be presented as flight-proven.
