# RescueSwarm GCS — Windows Build and Packaging

Status: the Vite/TypeScript production build is **BUILT and EXECUTED**. The NSIS
installer was **BUILT and the unpacked application launched successfully** on
2026-10-30; the installer itself has **not** been installed on a clean machine,
so end-to-end installation remains **PENDING TEST**. Do not claim a signed
installer or a clean-machine verification until that checklist is run.

---

## 1. Build the renderer

```bat
cd desktop
npm ci
npm run build
```

`npm run build` runs `tsc -b` and `vite build`. Output is `desktop\dist\index.html`
with hashed assets. The build must finish with zero TypeScript errors. A chunk
larger than 500 kB warning is expected (MapLibre GL); it is not an error.

The backend serves this directory when `RESCUESWARM_FRONTEND` points at it, so
the same UI runs in a browser and in Electron without a separate embed step.

---

## 2. Run the production-style shell unpackaged

```bat
cd desktop
set ELECTRON_RUN_AS_NODE=
npx electron .
```

Electron:

1. starts the Python backend from `..\backend` if no healthy service is already
   on `127.0.0.1:8765`;
2. sets data to `%APPDATA%\RescueSwarm GCS\data`;
3. serves `desktop\dist`;
4. blocks every network request except loopback, denies permissions, popups,
   webviews and navigation.

This path was smoke-tested: the window renders the station and the splash lists
local runtime versions.

---

## 3. Build the NSIS installer

```bat
cd desktop
npm run dist:win
```

This is equivalent to:

```bat
npm run build
electron-builder --win nsis
```

Configuration (`desktop/package.json` → `build`):

| Field | Value |
|---|---|
| `appId` | `in.adrodsx.rescueswarm.gcs` |
| `productName` | `RescueSwarm GCS` |
| output | `desktop\release` |
| packaged files | `dist/**`, `electron/**`, `package.json` |
| extra resources | `backend/`, `config/`, `samples/`, `desktop/dist` |
| target | NSIS, x64, non-one-click, per-user, selectable install dir |
| artifact | `RescueSwarm-GCS-<version>-<arch>.exe` |

Expected artifacts:

```
desktop\release\RescueSwarm-GCS-2.0.0-x64.exe
desktop\release\win-unpacked\RescueSwarm GCS.exe
```

The build was executed on 2026-10-30. Produced artifacts:

- `RescueSwarm-GCS-2.0.0-x64.exe` — 90 MB NSIS installer, unsigned.
- `win-unpacked\RescueSwarm GCS.exe` — 181 MB, 355 MB unpacked directory.

The unpacked executable was launched with its bundled frontend and backend
resources. It started its own backend, served the station from
`resources\desktop\dist`, showed the splash screen with Python 3.14.2, SQLite
3.50.4 and FFmpeg, and entered Mission setup. The NSIS installer was **not**
installed, and no clean-machine test was performed.

The first run of electron-builder downloads Electron and NSIS tooling; that
requires internet and may need elevated permissions for symlinks on Windows.
Record the exact command and outcome in `docs/TEST_REPORT.md` when you run it.

### Important packaging limitation

The installer ships the **backend source**, not a Python interpreter or wheels.
The target machine must have Python 3.11+ with `backend\requirements.txt`
installed, and `python` on `PATH` (or `RESCUESWARM_PYTHON` set). If the backend
cannot start, the shell shows a local error page and never falls back to a cloud
service. A self-contained build would require bundling a Python runtime
(PyInstaller or an embedded distribution) and is not implemented.

FFmpeg likewise must be installed on `PATH` for REAL video capture. The
simulator does not need it.

---

## 4. Verify the installer (checklist)

Do not mark the installer verified until every item passes on a clean Windows
10/11 x64 VM without Python installed first:

1. Run the `.exe`; choose an install directory; complete installation.
2. Launch **RescueSwarm GCS** from the Start menu.
3. The splash must show the local runtime versions, then the station UI.
4. Confirm the backend log path exists under
   `%APPDATA%\RescueSwarm GCS\logs`.
5. Disconnect the network and restart. The app must still launch, render the
   offline map and run a full simulated mission.
6. Install Python + requirements if the packaged app requires it; confirm the
   shell error message is actionable when Python is absent.
7. Uninstall; user data under `%APPDATA%` may remain (evidence is not deleted).

Record PASS/FAIL per item with date and machine details.

---

## 5. Code signing and distribution

The configuration is unsigned. Windows SmartScreen will warn on an unsigned
installer. Do not claim signing or reputation. If signing is added later, keep
the private key off the mission laptop and out of the repository; the application
does not require a signed binary to run offline.

---

## 6. Reproducibility notes

- Node dependencies are locked by `desktop\package-lock.json`; use `npm ci`.
- Python dependencies in `backend\requirements.txt` are unpinned. Pin exact
  versions before a competition freeze so a clean install matches the tested
  environment.
- The tested versions are listed in `docs/TEST_REPORT.md`.
- Do not add an auto-updater. The GCS must never fetch code during a mission.
