# RescueSwarm GCS — PRD/TRD Requirements Traceability

Date: 2026-11-01. Every requirement ID below is transcribed from
`docs/reference/RescueSwarm_GCS_Master_PRD_TRD_UIUX_Architecture_Report.md` §4.4 (FR-001…FR-094)
and §4.5 (NFR-001…NFR-012). Nothing in this table was inferred from the requirement text;
the *Implementation* and *Evidence* columns name the module, endpoint, screen or test that
was found in this repository, or state that none was found.

## How to read the statuses

| Status | Meaning |
|---|---|
| **MET** | Code exists, and a check in this repo exercises the requirement's behaviour. |
| **MET, HARDWARE UNVERIFIED** | Code exists and behaves correctly against the simulator or a test double. The acceptance condition as written needs real hardware or SITL, which this station does not have. |
| **PARTIAL** | Part of the requirement exists. The gap is named explicitly — this is not a synonym for "done". |
| **NOT IMPLEMENTED** | No code path was found for the requirement, or the code path exists but cannot reach the acceptance condition. |

Evidence labels follow the project vocabulary: **BUILT** (a test in `backend/tests/` passes),
**SIMULATED** (proved against the in-process simulator), **CALCULATED** (arithmetic verified,
not a flight fact), **PENDING TEST** (needs hardware/SITL), **DESIGNED** (documented, not coded),
**OPEN** (known and deliberately unfixed). A **BUILT** label on a UI row means the code was read
and the project typechecks and builds; there is no frontend automation, so nothing in the UI
column was *observed* running — see `TEST_RESULTS.md` §3a gap G-3.

Totals: 66 functional rows, 12 non-functional rows. **MET 41 · MET, HARDWARE UNVERIFIED 12 ·
PARTIAL 19 · NOT IMPLEMENTED 6.** The six unimplemented rows are FR-016, FR-032, FR-093, NFR-002,
NFR-003 and NFR-006. Each is carried in the gap list at the end with a fix-list row.

---

## A. Startup, configuration and device discovery

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-001 | Branded splash with app version, config version, SIMULATION/REAL choice; mode visible and persisted, never inferred | `desktop/src/screens/BootScreen.tsx`; `/api/health` + `/api/state` carry `config_version`; mode is a typed body field on `/api/fleet/connect` (`api.py:257`) | BUILT — matrix row 1, `test_api.py` | MET |
| FR-002 | Start and supervise local backend; service state visible; failure blocks dependents with an actionable error | `desktop/electron/main.cjs:105` spawns `python -m rescueswarm.main`, captures stdout/stderr to `logs/`, `child.on("exit")` → `gcs:backend-exit` to renderer + error page telling the operator to run the module; `preload.cjs:18` allow-listed listener; graceful stop over loopback before `taskkill /T /F` escalation | BUILT — `test_station_shutdown.py` (4 tests) | MET |
| FR-003 | Detect the RFD868x ground modem on USB/serial; show port, identity, settings, communication result | `backend/rescueswarm/mavlink.py:62` opens the configured serial port; `system.py:77 probe_serial_ports()` enumerates ports into `/api/system/devices` | The device is addressed by configured port only. `system.py:79` states *"none is assumed to be a paired RFD868x"* and `system.py:88` *"Topology is configured but not commissioned"*. No identity/descriptor read, no modem diagnostics. | MET, HARDWARE UNVERIFIED — port enumeration; `PENDING TEST` for identity and "communication result" |
| FR-004 | Detect the three capture devices; names, supported formats, live preview per input | `video.py:493 _enumerate_devices()` lists capture names; `/api/video/feeds` previews per feed | Device names and per-input preview: present. **Supported-format probing: not found** — the only format code in `video.py` is the writer's `mp4v` fourcc (`video.py:683`); the reader accepts whatever the OS hands it. | PARTIAL |
| FR-005 | Load saved profile for INS-01/DEL-01/DEL-02; role, sysid, vehicle type, sensor config visible | `config/fleet.json` (per-vehicle `callsign`, `role`, `sysid`, `vehicle_type`, sensor keys) surfaced through `/api/state.config` and `system.py:113 config_view()`; `SettingsScreen.tsx` renders it | BUILT — used by nearly every test | MET |
| FR-006 | Re-discovery after USB reconnect must not silently attach to the wrong aircraft or video panel | Video: each feed is re-bound by `device_fingerprint` (`video.py:157`, `video.py:196`), never by enumeration index; `test_video_capture.py::test_a_capture_device_is_only_ever_claimed_once`. Telemetry: `mavlink.py:62 autoreconnect=True` with `on_state(..., "RECONNECTING")` (`mavlink.py:106`) | The "wrong aircraft" guard for video is the fingerprint plus `manual.operator_video_identity` sign-off. Telemetry reconnect re-opens the *configured* port, so a port that moves to a different adapter requires the operator to notice. No USB-level re-discovery event is raised. | MET, HARDWARE UNVERIFIED |
| FR-007 | Validate the RFD network configuration against the approved project profile; unknown/mismatched firmware reported as unverified | `system.py:87` — the `radio` diagnostic is `PASS` only when `telemetry.topology_verified` is true, otherwise `WARN` with "not commissioned"; the flag is write-protected through the API | Profile-vs-device comparison of firmware version and network parameters: **not found** — no firmware string is read from the modem. The requirement is met in its negative half (unverified is reported as unverified, not as ready). | PARTIAL |

## B. Fleet discovery and telemetry

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-010 | Discover vehicles via MAVLink heartbeats; INS-01, DEL-01, DEL-02 separately identified | `telemetry.py` heartbeat path keyed by sysid → per-callsign state; simulator drives three distinct vehicles to `delivered=10` | SIMULATED — e2e run A | MET, HARDWARE UNVERIFIED |
| FR-011 | Verify sysid and vehicle type against configured roles; duplicates/missing/unexpected block readiness | Role/sysid matching in `telemetry.py`, blocking preflight checks in `preflight.py`; `test_real_mode_refuses_to_address_a_vehicle_it_cannot_see` | SIMULATED + BUILT | MET, HARDWARE UNVERIFIED |
| FR-012 | Independent per-aircraft position, mode, armed, battery, GPS, link health | `telemetry.py` per-callsign dicts from `GLOBAL_POSITION_INT`, `HEARTBEAT`, `SYS_STATUS`, `BATTERY_STATUS`, `GPS_RAW_INT`; 2 Hz persist (`telemetry.py:811`) | SIMULATED | MET |
| FR-013 | Detect stale telemetry and lost heartbeat; last-message time and state visible per aircraft | `stale_after_s: 2.0` (`config/fleet.json:72`); `LIVE`/`STALE`/`LOST` in `data_quality`; shown on vehicle cards and the map chip | BUILT + SIMULATED | MET |
| FR-014 | Display FC status/pre-arm text with the original message preserved and a plain-language explanation | Original text preserved: `telemetry.py:415` stores `state["statustext"]` and publishes `fleet.statustext` with `severity_raw`, `data_quality="LIVE"`, so it reaches the log stream. | **Plain-language explanation: not found.** The raw ArduPilot string is not translated, and no screen renders the per-aircraft `statustext` field — it is present in `desktop/src/lib/types.ts:36` but only `ReplayScreen.tsx:73` initialises it, so the operator sees it in the log feed, not next to the aircraft it came from. | PARTIAL |
| FR-015 | Monitor packet loss, latency and radio diagnostics where exposed; unavailable metrics labelled unavailable | `RADIO_STATUS` → `rssi` and a derived `link_quality` (`telemetry.py:410-412`); default `rssi: None` (`telemetry.py:164`) renders as unavailable, not estimated | **Packet loss, rx rate, and round-trip latency: not found in `telemetry.py`** (grep for `packet`, `rx_rate`, `latency` returns nothing). Link-margin figures from the RFD modem are absent. The honest-labelling half is met. | PARTIAL |
| FR-016 | Maintain a GCS heartbeat for the configured ArduPilot GCS failsafe; rate and failure behaviour verified with SITL and hardware | Nothing. `grep -rn "HEARTBEAT" backend/rescueswarm` finds only inbound parsing; the GCS never transmits a `HEARTBEAT` (component 190/`MAV_COMP_ID_MISSIONPLANNER`), so `FS_GCS_ENABLE` would see a permanently absent GCS | `ARCHITECTURE_REVIEW.md` finding **A-2**; `SAFETY_AND_FAILURE_ANALYSIS.md` **H-6**; `PRIORITISED_FIXES.md` row **27a**. Deliberately sequenced *before* failsafe provisioning, not silently skipped. | **NOT IMPLEMENTED** |
| FR-017 | Radio architecture abstraction: one shared RFD network or three independent P2P ports without touching the mission engine | `telemetry.topology` in `config/fleet.json` selects transport fan-in; the mission engine consumes `TelemetryManager` state only, never a socket | SIMULATED + BUILT. Note: the independent-port mode was silently dropping all traffic (CRITICAL C-“topology”), fixed this cycle. Topology is *configured*, `topology_verified` is false. | MET, HARDWARE UNVERIFIED |

## C. KML import and mission planning

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-020 | Import KML locally; parse offline; show filename, feature count, geometry | `kml.py` parse; `SetupScreen.tsx` area stage shows filename/feature count/area | BUILT — `test_kml*.py` | MET |
| FR-021 | Reject empty, invalid, self-intersecting or unsupported polygons with a clear explanation | Shapely validation in `kml.py`/`planner.py` | BUILT — `test_invalid_polygon_is_rejected`; matrix row 2 | MET |
| FR-022 | Compute area and extents in a suitable projected CRS, report units | `geo.py` local-projected area with units surfaced in the plan estimate | CALCULATED + BUILT | MET |
| FR-023 | Generate INS-01 search lanes clipped to a configured inward margin | `planner.py` lane generation using `lane_inset_m` from config; the inset is now printed in the plan panel instead of being hard-coded in prose | BUILT — `test_plan_lanes_and_swath`; SIMULATED coverage | MET |
| FR-024 | Per-aircraft geofence plans, each uploaded, acknowledged and read back individually | `planner.py:211` computes a per-vehicle `max_alt_m` fence; `upload.py` fences each callsign separately | SIMULATED — 9 read-back tests in `test_upload_readback.py`; matrix rows 6-9 | MET, HARDWARE UNVERIFIED |
| FR-025 | Delivery altitude and return profiles from approved config; show all altitude frames, home reference and terrain assumptions | `transit_alt_m`, `rtl_alt_m`, `search_alt_m` per vehicle from `config/fleet.json`; planner docstring: *"every assumption used in time and energy estimates"*; ceilings now rendered per fence | **Home reference and terrain assumption are not surfaced as a distinct field.** Altitudes are AGL-by-config; the GCS has no terrain model, and that is stated in the docs rather than shown in the plan panel. | PARTIAL |
| FR-026 | Validate waypoints, turns, pad and target points against the polygon; invalid points highlighted and not uploadable | `planner.py` containment + `boundary_drop_inset_m`; `preflight.py` blocking checks keep START locked; invalid markers surfaced in the map/area stage | BUILT + SIMULATED | MET |
| FR-027 | Search/delivery/recovery time estimates with inspectable inputs, labelled CALCULATED/ESTIMATE | `planner.py` estimate block with an `inputs` sub-object; `MissionPlan.estimate.inputs` is now typed and rendered in the Setup mission-profile panel; evidence labelled `CALCULATED` | BUILT — `test_plan_*`; fixed this cycle (hard-coded numbers removed from `SetupScreen.tsx`) | MET |
| FR-028 | Preview all aircraft routes and altitude layers together before authorisation | `TacticalMap.tsx` renders `aoi`, `lanes`, `fences`, `pad`, `links`, `trails` with per-callsign colours | **There is no altitude profile or altitude-layer view.** Ceilings appear as text; nothing plots a vertical cross-section. | PARTIAL |
| FR-029 | Save a versioned plan locally with the exact uploaded plan and config hash | `mission_plans` table (`db.py:75`) + deterministic plan hash | BUILT — `test_plan_hash_is_deterministic` | MET |

## D. Hardware and pre-flight inspection

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-030 | Role-specific INS-01 checks: Cube, GNSS, battery, Pi, Hailo, YOLO, camera, gimbal | Automated GNSS/battery/link checks in `preflight.py`; `manual.ins_camera` covers Pi Camera 3 Wide, gimbal and the Pi 5 / Hailo-8 / YOLOv8n chain as an operator sign-off | **Cube flight-controller health has no check** (automated or manual item). The AI-chain check is a human sign-off, not a measurement — the copilot cannot query Hailo. | PARTIAL |
| FR-031 | Role-specific delivery checks: Cube, GNSS, battery, TFmini-S, servo, break-beam, inventory | Automated GNSS/battery/rangefinder-presence/kit-count checks; `manual.rangefinder`, `manual.release_test` (servo + beam self-test), `manual.payload_inventory` | Same Cube gap as FR-030. Servo and break-beam are sign-off, which the brief requires (no sensor is being faked). | PARTIAL |
| FR-032 | Read ArduPilot pre-arm messages without bypassing them; a failed condition blocks the gate | The GCS receives `STATUSTEXT` and blocks on its own checks, but **never queries the autopilot's `ARMING_CHECK` bitmask nor reads the pre-arm failure list** (`grep -rn "ARMING" backend/rescueswarm` → no matches). Readiness is the GCS gate, not the FC's. | The FC will still refuse to arm on its own, so nothing unsafe ships; but the requirement as written ("read ArduPilot pre-arm messages") is not met and the operator is not shown why an arm would fail. Fix list row **32b** (new). | **NOT IMPLEMENTED** |
| FR-033 | Prove each live preview matches its aircraft during commissioning/preflight | `device_fingerprint` binding + `manual.operator_video_identity` sign-off recorded in `preflight_checks` | Proving it means putting a known pattern or call-out on each airframe — hardware only. | MET, HARDWARE UNVERIFIED |
| FR-034 | Separate automatic tests from physical sign-offs; physical checks cannot be auto-passed | `MANUAL_ITEMS` in `preflight.py:22-84`; sign-offs are per-actor, never defaulted | BUILT — matrix row 5 | MET |
| FR-035 | Check disk capacity before recording; insufficient space blocks readiness or safely disables recording | `evidence.py:87 disk_free_mb()` consumed by `preflight.py:274` and `system.py:69`; the recording gate refuses a feed that never delivered a frame | BUILT — `test_video_capture.py` (H-2 cluster) | MET |
| FR-036 | Keep complete inspection history with timestamp, source, measured value, evidence status | `preflight_checks` table (`db.py:58`) persists each result with its source and value | BUILT | MET |

## E. Mission upload and readiness

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-040 | Upload the search mission to INS-01; protocol completes, result stored | `upload.py` mission protocol + `connection_events`/`mission_events` rows | SIMULATED — e2e run A | MET, HARDWARE UNVERIFIED |
| FR-041 | Upload delivery config and fences to DEL-01/DEL-02, each checked individually | `upload.py` per-callsign items and read-back | SIMULATED — `test_upload_readback.py` | MET, HARDWARE UNVERIFIED |
| FR-042 | Read back and compare where supported; never claim verification from send-only status | Diffing per item; short read-back, shifted and same-length-different missions all rejected | BUILT + SIMULATED — matrix rows 8-9 | MET, HARDWARE UNVERIFIED |
| FR-043 | Fleet-wide readiness gate; START locked on any critical fail/unknown/stale | `preflight.py` gate; unknown ≠ pass | BUILT — matrix row 4 | MET |
| FR-044 | Final operator authorisation starts the mission timer and state machine | `/api/mission/start` typed confirmation + `X-Operator` header (`api.py:205`) | BUILT + SIMULATED | MET |
| FR-045 | Log every operator action: button, actor/session, timestamp, target, outcome | `operator_actions` table (`db.py:68`); `api.py:32 operator()` from the header; `operator.action` events at `api.py:177`, `226`, `284` carry action, target and operator | Actor is a free-text header, not an authenticated session — there is no login in this product. Recorded honestly in `SECURITY` notes. | PARTIAL |

## F. Live mission control and fleet management

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-050 | Three role-specific vehicle cards with task state, mode, battery, altitude, GPS, link, health | `MissionScreen.tsx` vehicle cards fed by `fleet` snapshot | BUILT (reviewed, not observed — G-3) | MET |
| FR-051 | Tactical map with boundary, geofence, drones, trails, lanes, survivor pins, delivery routes, pad | Layers `aoi`, `fences`, `links`, `trails`, `lanes`, `pad` + vehicle/survivor markers (`TacticalMap.tsx:154-205`) | Delivery routes are drawn as a straight assignment link, not the profiled waypoint path. Otherwise complete. | PARTIAL |
| FR-052 | Receive survivor reports from INS-01 with field and timestamp validation | `survivors.py` ingestion validating confidence, uncertainty and geotag; rejects non-finite/absurd values | BUILT — 8 tests, matrix row 19 | MET |
| FR-053 | Deterministic duplicate merging and survivor ID management, logged | `survivors.py` merge rules with `survivor_observations`; no geotag tug-of-war | BUILT | MET |
| FR-054 | Allocation by ETA, queue, kit availability, battery reserve, link and safety state | `allocation.py` lowest-ETA with one kit reserved; `allocation.py:89` `battery_reserve_pct`; offline/no-kit/unsafe rejection | SIMULATED + BUILT — matrix row 18 | MET |
| FR-055 | Transactional kit ledger: loaded/reserved/commanded/confirmed/unknown, no double allocation | `ledger.py`; `UNKNOWN` is never recycled; `payload_events` table | BUILT + SIMULATED — rows 14-15. Persistence of in-flight transactions across a GCS restart is **not** implemented (NFR-002). | PARTIAL |
| FR-056 | Delivery state machine with target, transit, clearance, descent, release, result separately represented | `delivery.py` states incl. `HOVER_VERIFY`; release only from hover with verification | SIMULATED — rows 12, 16; e2e 10/10 | MET |
| FR-057 | Reassign undelivered survivors after a delivery failure when safe; otherwise UNSERVED with reason | `mission_engine.py:435` retries only `UNSERVED`; `allocation.py:156/190/291` set UNSERVED with an explicit note; `delivery.py:78` maps `ABORTED → UNSERVED` | SIMULATED + BUILT | MET |
| FR-058 | Horizontal/vertical separation and stale-position monitoring; clearance denied when stale or outside limits | `safety.py:154 separation_warnings` (`proximity_warn_m`, `separation_min_m` range-guarded in `config.py:94`), stale checks in the clearance gate; `safety.py:165` suppresses the false 0 m ground-separation alarm | SIMULATED + BUILT — matrix row 20 | MET |
| FR-059 | Pad occupancy: one aircraft authorised at a time | `safety.py` pad interlock; recall blocks release | SIMULATED — matrix row 20 | MET |
| FR-060 | Fleet emergency recall and approved abort; every command ACK/state-verified; autopilot failsafes independent | Recall + abort implemented with per-command verification and kit reconciliation (`delivery.py`, `mission_engine.py`); `test_recall_clear_resumes_only_the_simulator`; abort e2e run B (18/18) | **The recall command itself is single-shot** — `attempts` stays 1 and no `RETRYING` path exists, so a lost recall packet is not re-sent (finding **H-7**, row **27b**, `OPEN` deliberately). Autopilot failsafe *independence* holds (the GCS cannot disable onboard protection), but GCS-failsafe provisioning is blocked by FR-016. | PARTIAL |

## G. Video and recording

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-070 | Three simultaneous live feeds visible on the same mission screen | `MissionScreen.tsx` renders all three panels from `/api/video/feeds` | SIMULATED — `test_video_capture.py`; BUILT for the panel (not observed, G-3); analog path not executable without hardware | MET, HARDWARE UNVERIFIED |
| FR-071 | Correct aircraft ID on each feed, validated in preflight, never guessed from device order | `video.py:196` matches by `device_fingerprint`; a feed with no fingerprint is unmapped rather than positionally assigned | SIMULATED + BUILT; the identity proof itself is commissioning hardware | MET, HARDWARE UNVERIFIED |
| FR-072 | Signal, frozen-frame and recording states shown separately; telemetry unaffected by video loss | `video.py:322-327` `FROZEN` after 2 s of identical frames; statuses `LIVE/RECORDING/FROZEN/LOST/UNAVAILABLE`; the three feeds and the fleet state machine are separate threads | BUILT — 13 tests; matrix row 23 | MET |
| FR-073 | Telemetry HUD overlay on video panels using timestamp-matched telemetry, indicating stale data | `video.py:444` burns callsign, receiver, mode, AGL, groundspeed, battery, a UTC stamp and the `SIMULATION — SYNTHETIC OPTICAL FEED` band into each frame | The overlay is drawn by the **synthetic** frame generator. On real capture the GCS forwards the received analog picture and cannot overlay it without re-timing the stream; values are read from current state, **not timestamp-matched to the frame, and staleness is not indicated on the overlay**. `latency_basis` reports `NOT_MEASURED`. | PARTIAL |
| FR-074 | Record each stream independently; one failure does not stop the others | Per-feed recorder threads + per-feed `cv2.VideoWriter`; recorder death is closed as incomplete, not propagated | BUILT — `test_a_recorder_that_died_mid_flight_is_closed_as_incomplete` | MET |
| FR-075 | Correlate video and mission events by timestamp for replay alignment | `video_sessions` (`db.py:149`) stores feed, fingerprint, recording path, mode, start/end; mission events carry ISO timestamps; replay aligns on time | The correlation is per-session, not per-frame, so alignment is bounded by session start/stop. The duplicate-PTS artefact in simulated recordings is recorded as a LOW finding. | PARTIAL |

## H. Reports, replay and diagnostics

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-080 | Log all three telemetry streams with source and receive timestamps | `telemetry_samples` (`db.py:38`) at 2 Hz with `source`/`data_quality` | BUILT + SIMULATED | MET |
| FR-081 | Log commands, acknowledgements, decisions and warnings | `mission_events`, `safety_events`, command lifecycle events `SENT → ACKNOWLEDGED → VERIFIED`, `ACK_UNVERIFIED` | SIMULATED — matrix rows 12-13; `test_command_lifecycle.py` (24) | MET |
| FR-082 | Save survivor and per-kit outcomes; export has all survivors and every kit's final status | `reporting.py:301-311` writes `survivors/survivors.csv` and `reports/kits.csv` per mission | BUILT — `scripts/e2e_simulation.py:117` asserts the artefacts exist | MET |
| FR-083 | Mission replay reconstructing positions and events over time | `ReplayScreen.tsx` + `/api/missions/{id}` replay data over `telemetry_samples`/`mission_events` | BUILT (reviewed, not observed — G-3) | MET |
| FR-084 | Offline report including verified data and separately labelled estimates | `reporting.py` JSON/Markdown/CSV/PDF with evidence labels per section | BUILT | MET |
| FR-085 | Export CSV, JSON and PDF saved locally with a mission identifier | `evidence.py:65 write_csv`, `reporting.py:147/296/365`, `api.py:614` `/api/missions/{id}/report.pdf` → `data/evidence/{mission_id}/reports/report.pdf` | BUILT | MET |

## I. AI assistant and advanced intelligence

| ID | Requirement (abridged) | Implementation found | Evidence | Status |
|---|---|---|---|---|
| FR-090 | Optional offline copilot answering only from local state, docs and logs | `copilot.py:25` — rule-based by default (`_rule_based`, `copilot.py:53`), optional localhost Ollama (`_ollama`, `copilot.py:286`) that degrades to the rule answer; no cloud dependency | BUILT — `test_engine_has_no_llm_dependency`, matrix row 25 | MET |
| FR-091 | Explain warnings and assignment decisions pointing to measured values and rule conditions | Branches for `allocat/rationale/assigned to` (`copilot.py:95`) and `anomaly/warning/alert` (`copilot.py:216`) quote state fields, `separations` counts and config rule values | BUILT — `test_copilot_is_read_only_and_answers_goal_prompts` | MET |
| FR-092 | Predict mission completion and battery reserve with assumptions and confidence shown; cannot override safety | Completion/time and reserve figures come from `planner.py` estimates (`planner.py:355 reserve_pct`) which the copilot's `time/eta/duration` branch (`copilot.py:231`) reports; `SAFETY_NOTE` is attached to every answer and there is no command path | **No confidence figure is produced** — the answer is a deterministic calculation, labelled CALCULATED rather than a prediction with a probability. Assumptions are inspectable, so the spirit is met and the letter is not. | PARTIAL |
| FR-093 | Detect abnormal telemetry *trends*; alerts identify the underlying signals and are not presented as hardware diagnoses | Nothing found. `grep -rni "trend"` across `backend/rescueswarm` returns no detector; the copilot's anomaly branch reports the *current* warning list, not a change over time. Battery-threshold logic (`copilot.py:269`) is a static limit comparison. | Fix list row **45** (new). | **NOT IMPLEMENTED** |
| FR-094 | Never let an LLM issue arbitrary flight or payload commands; commands go through a typed mission API, deterministic policy and interlocks | `copilot.py` has no MAVLink or payload import; `/api/copilot/ask` is read-only; the copilot cannot reach `release`/`set_mode` | BUILT — `test_no_endpoint_accepts_raw_mavlink`, `test_engine_has_no_llm_dependency` | MET |

## Non-functional requirements

| ID | Target | Implementation found | Evidence | Status |
|---|---|---|---|---|
| NFR-001 | All mission-critical functions work with the internet disconnected | No CDN, no tile server (procedural/OSM-pack tiles from `/api/map/tiles`), no cloud LLM (localhost-only Ollama, optional), Electron loads `dist/` from disk; `test_api.py` asserts the no-glyph-server and localhost host-header rules | BUILT. The acceptance condition is a **physical WAN-disconnect rehearsal of a full sortie**, which has not been run on this station. | MET (PENDING TEST for the rehearsal) |
| NFR-002 | Mission events and the kit ledger survive application restart / power interruption; test crash recovery | SQLite in WAL mode persists `kits`, `payload_events`, `mission_events`, `telemetry_samples`, `preflight_checks`, `operator_actions` | **In-flight command transactions are memory-only** (finding A-3; matrix row 35 `NOT IMPLEMENTED`), and no crash/power-loss test exists (gap G-4). The safe side is preserved — an interrupted transaction becomes `UNKNOWN`, never recycled. | **NOT IMPLEMENTED** (as specified: crash recovery untested, transaction journal not durable) |
| NFR-003 | Process isolation: telemetry, mission engine and each video input run independently; a video decoder crash must not crash the command/telemetry service | Telemetry reader threads, per-feed capture/recorder threads and the engine run as **threads inside one Python process**, guarded by try/except and watchdog closes (`test_a_recorder_that_died_mid_flight_is_closed_as_incomplete`) | A decoder crash is contained as a feed failure, but it is not *isolation*: the GIL couples cv2 decode with the telemetry reader (finding **A-9**, unquantified, `PENDING TEST`), and one OS process means one crash takes everything. | **NOT IMPLEMENTED** (thread-level containment only) |
| NFR-004 | 5 Hz UI refresh; actual telemetry latency and CPU load measured with all three aircraft | `/api/state` polled every 1400 ms plus WebSocket event push with a 350 ms debounced refresh (`GcsContext.tsx`) — the polling floor is ~0.7 Hz, with event-driven updates in between | **5 Hz is not met by the poll**, and latency/CPU have never been measured (G-2/G-3; no hardware). Fix list row **46**. | PARTIAL |
| NFR-005 | 2 Hz telemetry logging; high-value events logged immediately, never waiting for the periodic sample | 0.5 s persist interval (`telemetry.py:803`, `telemetry.py:811` per callsign); safety, command, delivery and operator events published immediately with `persist` defaults | BUILT + SIMULATED | MET |
| NFR-006 | All commands: ACK/result tracking, **bounded retries**, timeouts, unique transaction IDs, final-state verification | ACK correlation, per-command timeouts, unique transaction ids, and final-state verification all exist and are tested (24 lifecycle tests) | **Bounded retries are absent** — `attempts` stays 1, there is no `RETRYING` state (matrix row 33, `DESIGNED` only; finding **H-7**, row **27b** `OPEN`). This is the one clause of NFR-006 that fails, and it is the one that matters for recall. | **NOT IMPLEMENTED** (retry clause) |
| NFR-007 | No UI override may bypass ArduPilot pre-arm checks or configured failsafes | The GCS has no path that clears a blocking preflight check, no `ARMING_CHECK` override, and no parameter write that disables a failsafe; manual items require an explicit operator header | BUILT — matrix rows 4-5; the copilot cannot write params | MET |
| NFR-008 | Primary mission state and critical alerts visible without nested screens; layout tested at 1920×1080 and 2560×1440 | `MissionScreen.tsx` keeps cards, map, alerts, and ABORT/RECALL on one surface; alert banner is not modal-gated | Layout was reviewed at 1920×1080 in this audit. **No test exists at 2560×1440**, and there is no UI automation at all (G-3). | PARTIAL |
| NFR-009 | High-contrast light theme for sunlight; avoid low-contrast translucent panels outdoors | `[data-theme="light"]` with its own palette (`index.css:48`), persisted in `localStorage`; theme toggle in the shell | BUILT. **Contrast ratios were not measured** with a contrast tool, so "high-contrast" is a design claim here, not a verified one. | PARTIAL |
| NFR-010 | Each service documented interface, structured logs, health endpoint, unit/integration tests | `docs/API.md` covers all 53 HTTP endpoints over 50 distinct paths plus `/ws` (route table diffed against `api.py` on 2026-11-01), `/api/health`, a structured event bus with severity and `data_quality`, logs under `logs/` and the evidence folder, 118 backend tests + e2e harness | BUILT. `API.md` gained `POST /api/system/shutdown`, `GET /api/map/manifest`, the lifecycle wording and the `{mission_id}` path parameter name in this cycle | MET |
| NFR-011 | Electron context isolation, sandboxing, strict IPC validation, no unrestricted renderer Node access, no external content in REAL mode | `main.cjs:179-183` sets `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, `webSecurity: true`, `allowRunningInsecureContent: false`; `preload.cjs` exposes a three-member allow-list (`runtimeInfo`, `openDataFolder`, `onBackendExit`); loopback-only bind with host-header check | BUILT — `test_api.py`, matrix row 26 | MET |
| NFR-012 | Every displayed number has provenance: live, stale, calculated, assumed, simulated, manual sign-off or unavailable | `data_quality` on every event and vehicle record; `evidenceQuality(state)` chip; map chip now distinguishes `LIVE` / `RECORDED` / `SIMULATED` / `NO TELEMETRY` / `MIXED` / other rather than collapsing to "SIMULATED"; mission-profile numbers now come from config instead of hard-coded strings; `latency_basis: NOT_MEASURED` | BUILT + fixed this cycle. Residual: not *every* number carries a label — the HUD overlay (FR-073) and log-screen aggregates are the known exceptions. | PARTIAL |

---

## Gap list — requirements this build does not meet

Ordered by the safety consequence of the gap, not by requirement number.

1. **FR-016 GCS heartbeat — NOT IMPLEMENTED.** The station never transmits `HEARTBEAT`, so a
   configured ArduPilot GCS failsafe has nothing to watch. Consequence: `FS_GCS_ENABLE` must stay
   at 0, and the airframe's response to a silent GCS is currently the RC link only. Tracked as
   A-2 / H-6 / fix row 27a, `OPEN`; it is sequenced *before* failsafe provisioning on purpose.
2. **NFR-006 bounded retries — NOT IMPLEMENTED.** A recall or abort packet lost on the radio is
   not re-sent (`attempts` = 1, no `RETRYING`). This is the same defect seen from the requirements
   side: H-7 / fix row 27b.
3. **FR-032 ArduPilot pre-arm read — NOT IMPLEMENTED.** The GCS never reads `ARMING_CHECK` or the
   FC's pre-arm failure text, so "ready" means "the GCS's own checks pass", not "the autopilot
   would arm". New fix row **32b**.
4. **NFR-002 restart/power-interruption durability — NOT IMPLEMENTED as specified.** Durable where
   it is tested, memory-only for in-flight transactions, and no crash test exists (G-4).
5. **NFR-003 process isolation — NOT IMPLEMENTED as specified.** Threads, not processes; contained
   failure rather than isolation (A-9).
6. **FR-093 telemetry trend detection — NOT IMPLEMENTED.** Thresholds only; no drift detection. New
   fix row **45**.
7. **FR-015 / FR-004 / FR-007 (PARTIAL, radio and capture diagnostics).** No packet-loss or latency
   metric; no capture-format enumeration; no firmware/network-parameter comparison. The build labels
   all three unavailable instead of estimating them, which is the honest half of the requirement.
8. **FR-014 / FR-073 / FR-075 (PARTIAL, presentation gaps).** Raw pre-arm text is captured but not
   translated or shown per aircraft; the HUD overlay is synthetic-only and not timestamp-matched;
   video↔event correlation is per session.
9. **FR-025 / FR-028 / FR-051 / FR-092 (PARTIAL, planning and display gaps).** Home/terrain
   reference not surfaced as a field; no altitude-layer view; delivery route drawn as a straight
   link; no confidence figure on predictions.
10. **FR-030 / FR-031 (PARTIAL).** No Cube flight-controller check, automated or manual.
11. **FR-045 (PARTIAL).** Operator is a header string, not an authenticated session.
12. **FR-055 / FR-060 (PARTIAL).** Ledger is transactional in memory with `UNKNOWN` on the unsafe
    side, but durability and recall re-send are the gaps above.
13. **NFR-004 / NFR-008 / NFR-009 / NFR-012 (PARTIAL, verification gaps).** 0.7 Hz poll floor vs the
    5 Hz design target; no 2560×1440 check; contrast unmeasured; provenance not yet on every number.

## What this matrix deliberately does not claim

Meeting a row here means the code implements the requirement and a check in this repository
exercises it. It does **not** mean the corresponding physical behaviour has been observed: twelve
rows are marked `MET, HARDWARE UNVERIFIED`, and the acceptance conditions for NFR-001 (WAN
disconnect), NFR-004 (latency/CPU with three aircraft), FR-016 (SITL and hardware) and FR-033
(receiver-to-aircraft proof) all require hardware or SITL that this station does not have. No row
in this document should be quoted as evidence that the GCS is flight-ready.
