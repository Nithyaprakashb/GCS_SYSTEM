# RescueSwarm GCS — Known Issues and Limitations

This is an honest list of what is unfinished, unverified or deliberately
excluded. Evidence labels apply: **PENDING TEST** means no hardware test was run;
**BUILT** means the code exists; **DESIGNED** means only a plan or protocol
exists. Nothing here should be hidden in a demo.

---

## 1. Hardware path — PENDING TEST

1. **No real RFD868x link has been established.** The shared multipoint topology
   is unverified (`topology_verified: false`). No pairing is inferred from USB
   detection. Independent point-to-point is the safe fallback but is also
   untested on the actual radios.
2. **No real ArduPilot mission, fence or parameter upload has been executed.**
   The MISSION_COUNT → REQUEST_INT → ITEM_INT → ACK and read-back sequence is
   implemented and unit-exercised, but not against a flight controller.
3. **No arm, takeoff, RTL, land or servo release has been commanded on
   hardware.** All ACKs observed so far are simulated.
4. **TFmini-S AGL and the 3 m release clearance are untested.** The release path
   depends on a valid height measurement.
5. **IR break-beam polarity and reliability are untested.** A missing beam
   correctly produces `UNKNOWN`, but the physical sensor wiring has not been
   validated.
6. **TX800 capture and the three-device mapping are unconfigured**
   (`device_fingerprint: null`, `mapped: false`). Video readiness blocks REAL
   missions until commissioning.
7. **The Pi 5 + Hailo-8 YOLOv8n pipeline is external.** The GCS validates and
   merges survivor reports, but the onboard detector was not run or measured in
   this workspace. Detection accuracy is not claimed.
8. **No HIL and no flight test.** SITL instructions exist but ArduPilot SITL was
   not installed here, so SITL itself is **DESIGNED**, not executed.

---

## 2. Packaging — PENDING TEST

1. **The Windows NSIS installer was built but not installed.** The 90 MB
   `RescueSwarm-GCS-2.0.0-x64.exe` exists and the unpacked app launched
   successfully, but clean-machine installation and uninstallation are
   **PENDING TEST**.
2. **Python is not bundled.** The packaged app ships backend source but requires
   Python 3.11+ with dependencies on the target machine. A self-contained build
   (PyInstaller/embedded Python) is not implemented.
3. **FFmpeg is not bundled.** REAL capture requires FFmpeg on `PATH`.
4. **The installer is unsigned.** SmartScreen will warn; no code-signing or
   reputation is claimed.
5. **No auto-update.** This is deliberate: the GCS must not fetch code during a
   mission. Updates are manual.

---

## 3. Simulation realism

1. **The simulator is deterministic and simplified.** It models lanes, survivors,
   batteries, link faults, kit release and break-beam outcomes; it is not a
   physics-grade flight model.
2. **Simulated video is synthetic.** The panels are coarse generated frames
   labelled SIMULATED, not recorded RunCam footage. They are honest but visually
   basic.
3. **Simulated receipts, telemetry and deliveries are labelled.** They must never
   be copied into a report as REAL evidence. The readiness gate refuses simulated
   receipts in REAL mode.
4. **Detection noise and break-beam failure probability** are configurable
   simulation parameters (seed 71, 8 % beam failure by default); they do not
   represent measured hardware reliability.

---

## 4. AI and autonomy limits

1. **No YOLO bounding boxes are drawn on analog video.** The Pi camera and the
   RunCam have no validated frame synchronisation. Survivor locations are shown
   as geotagged registry entries, not as overlay boxes on the video.
2. **The copilot is deterministic and read-only.** It cannot send MAVLink,
   release a kit, change configuration or bypass an interlock. An optional local
   Ollama model may rephrase answers; it is disabled by default, is untested
   here, and receives only read-only context.
3. **Allocation is a greedy deterministic ETA choice, not a global optimiser.**
   It selects the lowest feasible delivery ETA and reserves one kit atomically.
   It does not solve a multi-vehicle routing optimum across all survivors.
4. **No automatic retry after an unknown delivery.** This is intentional. A
   missing beam is surfaced to the operator and never silently retried.

---

## 5. Functional gaps

1. **No operator account system.** `X-Operator` is a self-declared label for the
   audit trail, not authentication. The app relies on the loopback bind and
   Host allow-list.
2. **No cloud sync, remote viewing or multi-station collaboration.** All excluded
   by the offline-first requirement.
3. **Map tiles are procedural, not survey imagery.** They convey geometry and
   context offline; they are not cadastral or satellite maps. No internet tiles
   are used at any zoom.
4. **PDF reports are intentionally minimal.** The in-tree PDF writer produces
   text/tables without embedded video frames or map images.
5. **No terrain awareness or obstacle database.** Separation checks use reported
   positions and configured minima, not terrain elevation.
6. **Video latency measurement is approximate.** Health flags freeze, black,
   loss and latency, but latency is derived from the local pipeline, not a
   time-coded analogue signal.
7. **Single-instance lock.** A second Electron launch focuses the first window;
   it does not open a second station.
8. **Port conflict handling is manual.** If 8765 is occupied by an unrelated
   service, set `RESCUESWARM_PORT` for both backend and shell.
9. **Command retries are not implemented.** `telemetry.command_retries` is
   reserved but unused; a command is sent once and a missing ACK becomes
   `TIMEOUT`. This is safer than blind retries for release, but it means a
   transient serial drop requires operator re-action or the mission state
   machine's next transition.

---

## 6. UI and usability

1. **The mission right column can require scrolling** at 980 px height to reach
   warnings, AI explanations and the autonomy timeline. The map and video panels
   keep priority; on smaller laptop screens the operator must scroll.
2. **All three video panels are small** in the mission grid. They are sufficient
   for situational awareness, not for detailed inspection; the setup screen and
   individual feed endpoints provide larger views.
3. **The main JS chunk is ~1.4 MB** (MapLibre GL). Vite warns about the 500 kB
   threshold. Load time is local and acceptable, but code-splitting is not done.
4. **Simulation speed control is limited to the configured presets** exposed in
   the UI; changing speed requires the simulation configuration, not a live
   scrubber.
5. **No touch-first layout.** The station targets mouse and keyboard with
   accessible focus states; it is not a tablet app.

---

## 7. Deliberate non-features

These are absent for safety, not forgotten:

- No force-start, no gate bypass, no safety-interlock override.
- No raw MAVLink console for the renderer.
- No route to disable ArduPilot failsafes.
- No payload release without ledger reservation, hover verification and
  break-beam resolution.
- No automatic retry after `UNKNOWN`.
- No internet map tiles, cloud account, telemetry upload or online LLM
  requirement.
- No placeholder buttons that pretend to work.

---

## 8. Priority before competition

1. Commission radios and prove the topology; fill firmware/region fields.
2. Map the three capture devices and prove recording per feed.
3. Run the SITL bench, then prop-off upload/parameter tests.
4. Verify TFmini-S height and servo/beam behaviour with a dummy load, tethered.
5. Produce and test the NSIS installer on a clean machine, with Python and FFmpeg
   prerequisites documented.
6. Only then attempt supervised flight testing with a safety pilot.
