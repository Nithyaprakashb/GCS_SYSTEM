# RescueSwarm GCS — Prioritised Fix List

Order follows the review brief's own priority: critical safety and communication
defects first, then broken core features, incorrect state transitions, hardware
mapping, incomplete workflows, UI usability, visual polish, advanced AI.

Every entry carries the evidence label of how it was proven. Nothing here is
`PHYSICALLY TESTED`; that label is reserved for work done against real aircraft, modems
and receivers, none of which has happened.

Legend: **FIXED** = defect removed and pinned by a regression test that was shown to
fail before the fix. **OPEN** = real defect or gap left in place, with the reason.
**PENDING TEST** = implemented, needs hardware or a bench to prove.

---

## Priority 1 — critical safety and communication

| # | Defect | Where | Status | Proof |
|---|---|---|---|---|
| 1 | An accepted command never reached a terminal state, so `ACK_PENDING` never settled and a payload release could not complete | `telemetry.py` `TERMINAL_TX_STATES`/`_sweep_transactions`/`_settle` | FIXED | `SIMULATED` — 4 tests in `test_command_lifecycle.py`; 8-failed baseline recorded in `TEST_RESULTS.md` |
| 2 | Independent (per-aircraft) modem topology discarded every non-heartbeat message | `telemetry.py` `_on_mavlink` | FIXED | `SIMULATED` — 2 tests, same file |
| 3 | A release frame that left the station could be returned to the magazine and promised to a second survivor | `telemetry.py` `transmitted`, `delivery.py` `settle_release_transaction`, `ledger.py` `note_command_abandoned`, `mission_engine.py` `_reconcile_kits_on_abort` | FIXED as a set | `SIMULATED` — 3 tests |
| 4 | Payload release was not blocked while emergency recall was active | `safety.py` `release_clearance` | FIXED | `SIMULATED` — `test_recall_blocks_a_payload_release` |
| 5 | REAL mode could attempt to address an aircraft it cannot see | `telemetry.py` `_address_block` | FIXED (earlier cycle) | `SIMULATED` — `test_real_mode_refuses_to_address_a_vehicle_it_cannot_see`, `test_real_mode_gate_needs_mavlink_not_cached_simulation` |
| 6 | Safety thresholds relaxable through the config API; `topology_verified` patchable | `config.py` `validate_patch`, `safety_keys`, `/api/system/config` | FIXED (earlier cycle) | `BUILT` — 4 config tests |
| 7 | A GCS restart must not leave onboard protections dependent on the station | `docs`/design: RTL-on-link-loss is an autopilot parameter, not a GCS behaviour | OPEN by design | `PENDING TEST` — must be proven on the aircraft; the GCS never claims it |

## Priority 2 — broken core features

| # | Defect | Where | Status | Proof |
|---|---|---|---|---|
| 8 | Mission-blocking work ran on the event loop; `/video/stop` could stall every request behind a lifecycle lock | `api.py` `start_mission`, `recall`, `recall_clear`, `mission_reset`, `video_stop` | FIXED | `BUILT` |
| 8a | **Regressed by 8, then fixed:** the simulated fleet could no longer start. `asyncio.create_task` in a worker thread has no running loop; the coroutine was never awaited and the mission sat at `EXECUTING` with nothing flying it. Caught by the end-to-end run, not by the unit suite. | `simulation.py` `start`/`start_bench`/`_schedule_run`, `events.py` `bound_loop` | FIXED | `SIMULATED` — `tests/test_threading.py` (2 tests, shown failing with `RuntimeError: no running event loop` before the fix) plus `tools/sim_e2e.py` |
| 9 | A feed was declared `LIVE` before a frame existed, and preflight's `fleet.video` gate accepted it | `video.py` `start`, `_health_loop`; `preflight.py` | FIXED | `SIMULATED` — `test_a_started_but_silent_feed_is_never_reported_live` |
| 10 | Recording accepted a feed that had never produced a frame; the `video_sessions` row was opened and never closed | `video.py` `start_recording`/`stop_recording`, `db.py` | FIXED | `SIMULATED` — 4 tests; the test that previously pinned the wrong behaviour was corrected |
| 11 | Start/stop video were not mutually exclusive, so an orphan ffmpeg kept the DirectShow device | `video.py` `_lifecycle_lock` | FIXED | `SIMULATED` — contention and teardown tests |
| 12 | Abort returned `None` to the client instead of a body | `mission_engine.py` `abort` | FIXED (earlier cycle) | `SIMULATED` — `test_abort_answers_with_a_body_and_leaves_no_kit_reserved` |

## Priority 3 — incorrect state transitions

| # | Defect | Where | Status | Proof |
|---|---|---|---|---|
| 13 | Release could be commanded from a state other than `HOVER_VERIFY`; double release | `delivery.py`, `ledger.py` | FIXED (earlier cycle) | `SIMULATED` — `test_release_cannot_be_commanded_from_the_wrong_state`, `test_double_release_is_blocked_by_ledger` |
| 14 | Beam evidence could mark a kit delivered without a release, and could be re-applied twice | `ledger.py` `apply_beam_result` | FIXED (earlier cycle) | `SIMULATED` — `test_beam_evidence_without_release_is_refused`, idempotency index in `db.py` |
| 15 | An ACK from a companion component (camera, gimbal) was accepted as autopilot acceptance | `telemetry.py` `_on_command_ack` | FIXED | `SIMULATED` — `test_command_ack_from_a_non_autopilot_component_is_ignored` |
| 16 | A delivered survivor could be reopened by a late report; a merge could move a geotag an aircraft is committed to | `survivors.py` | FIXED (earlier cycle) | `SIMULATED` — `test_survivor_registry.py` |
| 17 | Frontend phase rail disagreed with the backend machine (missing `ACK_PENDING`, `CONFIRMED`, `UNKNOWN`, `ABORTED`; `HOLD_CLEARANCE` misplaced) | `MissionScreen.tsx` `DELIVERY_SPINE` | FIXED | `BUILT` — names taken from `delivery.py` `STATES` |
| 18 | Link change mislabelled an accepted command as a plain timeout | `telemetry.py` `_abandon_pending_transactions` | FIXED | `SIMULATED` — `test_link_change_labels_an_accepted_command_by_what_actually_happened` |

## Priority 4 — hardware mapping

| # | Item | Where | Status |
|---|---|---|---|
| 19 | Payload servo channel/PWM (`release_servo_channel: 9`, `release_pwm: 1900`) are profile values that have never been read back from an aircraft | `config/fleet.json`, `docs/HARDWARE_CONFIG.md` | `PENDING TEST` — bench: command the channel, measure the pulse on the servo lead, confirm direction and travel |
| 20 | RFD868x multipoint vs three independent modems: `telemetry.topology: "shared"`, `topology_verified: false` and API-protected | `config.py`, `preflight.py` | `PENDING TEST` — physical commissioning record; the GCS refuses to claim it |
| 21 | TX800 receive: no capture device has ever been enumerated at this station | `video.py` REAL path | `PENDING TEST`; the fake-ffmpeg tests prove decisions, not the analog link |
| 22 | Feed-to-aircraft identity is a commissioned `device_fingerprint`; enumeration order is never accepted | `video.py` `_start_locked`, `device_snapshot` | FIXED/`BUILT` — `test_a_capture_device_is_only_ever_claimed_once` |
| 23 | `MAV_CMD_DO_SET_SERVO` has no `SERVO_OUTPUT_RAW` expectation, so a release is proven by beam evidence only | `telemetry.py` `_expectation_for` | OPEN, documented as a deliberate choice — see `MAVLINK_COMMUNICATION_REVIEW.md` |
| 24 | Geofence: circle fence (`FENCE_TYPE 5`) is not enabled without a radius; action/`FENCE_ACTION` handling | `upload.py`, `docs/MAVLINK_MAPPING.md` | FIXED (earlier cycle) for the refusal; `PENDING TEST` on a real autopilot |
| 25 | TX800 glass-to-glass latency has never been measured | `video.py` `latency_basis` | OPEN/`PENDING TEST`; reported as `NOT_MEASURED`, never invented |

## Priority 5 — incomplete workflows

| # | Item | Where | Status |
|---|---|---|---|
| 26 | Commands are single-shot: `attempts` stays 1 and `RETRYING` is never set | `telemetry.py` `send_command` | OPEN — deliberate for payload release (a blind re-fire is worse than a timeout); a waypoint-upload retry policy is the honest next step |
| 27 | Transactions are not persisted, so an ACK arriving after a GCS restart cannot settle the release | `telemetry.py`, `db.py` | OPEN — the kit is marked `UNKNOWN`, which is the safe outcome; the audit trail loses the resolution |
| 28 | A survivor left `UNSERVED` by an abort is not re-allocated automatically | `mission_engine.py` `_reconcile_kits_on_abort` | OPEN by design — re-tasking after an abort is the operator's decision; recorded so it is not mistaken for a silent bug |
| 29 | Freshness is refreshed by any MAVLink message, not only heartbeats | `telemetry.py` | OPEN — `heartbeat_age_s` is now surfaced so the operator can see the difference |
| 30 | Station shutdown exists (`/api/system/shutdown`) and refuses while a sortie executes | `api.py`, `system.py` | FIXED (earlier cycle) — `test_station_shutdown.py` |
| 31 | Log screen reads the in-memory ring only; the durable history is `/api/missions/{id}/events` | `LogsScreen.tsx` | OPEN — documentation-level, listed in `UI_UX_REVIEW.md` |

## Priority 6 — UI usability

See `UI_UX_REVIEW.md` for the per-screen findings and scores. Summary of what was
changed in this cycle: command lifecycle panel; delivery spine matching the backend;
mission time cap and fleet/video counts read from state instead of constants; link cell
now shows heartbeat age next to radio quality; map provenance chip (live / simulated /
recorded); video panel says "no frame yet" and how long the feed has been silent;
`statusTone` covers the confirmed and video states.

The following were found in the second UI pass and are now closed:

| # | Defect | Where | Status | Proof |
|---|---|---|---|---|
| 32 | The confirmation dialogs had no accessible name and no description: `role="dialog"` with `aria-modal` but no `aria-labelledby`/`aria-label`, so a screen reader announced "dialog" and nothing else — on the abort and recall confirmations specifically | `ui.tsx` `Modal` | FIXED | `BUILT` — title is now wired by id (`aria-labelledby` when the title is a string, `aria-label` fallback for node titles), body wired via `aria-describedby`; `Escape` closes only the topmost of stacked dialogs through a module-level stack instead of every listener at once |
| 33 | The map's telemetry chip derived its label from `mission.mode`, so a REAL session whose links had all dropped — or a fleet that was half live, half lost — was printed as `LIVE TELEMETRY` | `MissionScreen.tsx` (both maps), `SetupScreen.tsx` (both maps) | FIXED | `BUILT` — chip now takes `evidenceQuality(state)` from `lib/format`, which reads each vehicle's `data_quality`; the helper is shared, so a screen cannot re-derive it wrongly |
| 34 | Any quality that was not exactly `LIVE` or `RECORDED` was rendered as `SIMULATED — NOT FLIGHT DATA`, which told an operator watching a stale or lost link that the map was a simulation | `TacticalMap.tsx` `provenanceChip` | FIXED | `BUILT` — `STALE`, `LOST`, `UNAVAILABLE`, `MIXED (…)` and `NO TELEMETRY` each get their own wording and tone; the raw quality string is displayed, not inferred |
| 35 | Emergency `ABORT` / `EMERGENCY RECALL` carried the native `disabled` attribute when a precondition was unmet, so the press did nothing and gave no reason — the control an operator most needs stayed silent | `App.tsx` `requestEmergency`, `index.css` `.is-blocked` | FIXED | `BUILT` — the buttons stay focusable and clickable, are marked with `.is-blocked` plus `aria-disabled` and a `title`, and the click now raises a toast naming the exact precondition. A disabled safety control that cannot explain itself is not reachable |
| 36 | The setup screen advertised `12 m AGL`, `8 m/s`, cap `10`, merge `5 m`, clearance `20 m`, a `23.7 m` swath, a `25 m` fence ceiling and a `2 m` inset as literals. Change the profile and the UI keeps promising values the safety rules no longer enforce | `SetupScreen.tsx` `AreaStage`, `PlanStage` | FIXED | `BUILT` — read from `plan.estimate.inputs`, `plan.search`, `plan.geofences[].max_alt_m` and the new `mission.limits`; absent values render `—` with an explicit note instead of a default. The backend publishes `limits` from the same config keys `safety.py` and `survivors.py` consult |
| 37 | The report-download footer in the replay modal fell back to `href="#"`, a dead control styled as a primary button | `ReplayScreen.tsx` | FIXED | `BUILT` — the anchor renders only when a mission is selected; otherwise a plain instruction line |
| 38 | No `prefers-reduced-motion` handling: the radar sweep, blip pulses, spinners and modal slide-ins run continuously and cannot be reduced by the OS setting | `index.css` | FIXED | `BUILT` — animations and transitions collapse to their end state; colour, layout and information are untouched, so no state becomes hidden |

## Priority 6b — the basemap and what it claims

| # | Item | Where | Status | Proof |
|---|---|---|---|---|
| 39 | The offline map was procedural terrain — a deterministic noise field, not the ground. Acceptable as a reference surface, wrong as a basemap for a search area | `map_tiles.py`, new `tools/offline_map.py`, `tools/osm_basemap.py` | FIXED | `BUILT` — 1,572 tiles (z10–z16, dark and light, 16.5 MiB) rendered from `map/india-261030.osm.pbf` (Geofabrik, extract timestamp 2026-10-30T21:24:51Z) through pyrosm 0.28; 469,124 OSM features. A tile pack is read from disk before the procedural renderer, so no route changed |
| 40 | Nothing on screen told the operator which surface was under the mission geometry, or that the fallback was invented terrain | `map_tiles.py` `provenance()`, `GET /api/map/manifest`, `style()` attribution | FIXED | `BUILT` — provenance publishes source, data date, coverage, zoom range, simplifications and `network_access: false`; the style's `maxzoom` now stops at the pack top so MapLibre over-zooms the real tile instead of requesting a z17 that the server would fill with procedural noise |
| 41 | The `.pbf` itself (1.6 GB) and the generated tiles must never enter the repository | `.gitignore` | FIXED | `BUILT` — the extract and tile output are ignored; the pack is reproducible from the two documented commands |

## Priority 5b — gaps found by the requirements traceability pass

Surfaced by walking `REQUIREMENTS_TRACEABILITY.md` row by row against the code, not by reading
the UI. Each row names the requirement ID it blocks, so a future change can be checked against
both documents.

| # | Item | Where | Status | Proof |
|---|---|---|---|---|
| 32b | The readiness gate is the *GCS's* gate. It never reads the autopilot's own pre-arm state — no `ARMING_CHECK` query, no pre-arm failure list — so FR-032 ("read ArduPilot pre-arm messages without bypassing them") is unmet even though nothing bypasses them | `upload.py` `_parameter_plan`, `preflight.py` | **NOT IMPLEMENTED.** The FC still refuses to arm on its own, so the gap is informational rather than dangerous — but an operator who sees `READY` and then a ground station that will not arm has been misled twice. Fix = query the arming-check bitmask and `ARMING_SKIP` *absence*, display the FC's own reason text next to the GCS's | blocks FR-032; sequenced with 27a, since both need the same outbound command path |
| 42 | Raw `STATUSTEXT` is captured and event-logged but never translated and never shown per aircraft: the field exists in `types.ts:36` and no screen renders it, so pre-arm and EKF warnings reach the operator as a line in a shared log stream | `telemetry.py:415`, `MissionScreen.tsx` | OPEN | blocks FR-014. The plain-language half is a small lookup table; the per-aircraft half is a card field |
| 43 | The video HUD is drawn by the *synthetic* frame generator, reads current state rather than the state at the frame's timestamp, and does not indicate staleness; real analog frames pass through un-overlaid | `video.py:444`, `video.py` stream path | OPEN | blocks FR-073. Depends on the `latency_basis` work in row 25 — an overlay that claims a match it has not measured is worse than no overlay |
| 44 | Device honesty at the edges: capture formats are never enumerated (the reader accepts whatever the OS provides), the RFD modem's identity and firmware are never read, and no packet-loss / rx-rate / latency metric exists for the radio | `video.py:493`, `system.py:77`, `telemetry.py` | OPEN | blocks FR-004, FR-007, FR-015. All three are labelled *unavailable* today rather than estimated, which is the acceptable half of the requirement |
| 45 | Abnormal-trend detection does not exist. Battery and EKF logic compares a value to a limit; nothing looks at the *direction* of a series, so FR-093 is unmet | `copilot.py:216`, `safety.py` | OPEN | blocks FR-093. The minimum honest version is a bounded-window slope test on battery and EKFX variance, reported as a signal, never as a hardware diagnosis |
| 46 | Two presentation targets are missed and were never measured: the UI's polling floor is 1400 ms (≈0.7 Hz) against the 5 Hz design target, and no layout check exists at 2560×1440 | `GcsContext.tsx`, `desktop/src/screens` | OPEN | blocks NFR-004, NFR-008. The WebSocket already pushes events; the gap is the snapshot-refresh floor. Latency/CPU with three aircraft stays `PENDING TEST` |
| 47 | Durability and isolation are stated as achieved where the code provides containment: the transaction journal is memory-only (row 27) and telemetry, engine and video share one Python process under one GIL (finding A-9), with no crash-recovery test | `telemetry.py`, `db.py`, `video.py` | OPEN | blocks NFR-002, NFR-003. Row 26a must land before a durable journal can be correct, and A-9 needs a measurement before a process split can be justified |

## Priority 7 — visual polish

- `BootScreen` warn row used a tick glyph in amber — a success shape on an unproven
  check. Now `TriangleAlert`, with `.state.warn` colouring added. FIXED.
- The header link dot carried its tone on `.pip` while the CSS only matched
  `.fleet-dot.ok .pip`, so it never changed colour. FIXED.
- Remaining polish items (type scale rhythm, panel density on the mission screen,
  contrast of `--faint` at 9.5 px) are listed in `UI_UX_REVIEW.md`.

## Priority 8 — advanced AI

- The copilot is advisory-only (`copilot.advisory_only` protected), has no MAVLink or
  payload import, and is kept out of the mission path by
  `test_engine_has_no_llm_dependency`. `BUILT`.
- No AI feature is presented as a detection pipeline that has been validated on
  real imagery. Onboard Hailo/YOLO work is a separate repository and is `ASSUMED`
  here; nothing in the GCS claims its performance.
- Deliberate non-goal: the interface stays a control station. No chatbot-style
  central panel, no AI-authored commands.

---

## What is left, in one list

1. `PENDING TEST` hardware items 19-21, 24, 25 and the onboard failsafe provisioning
   (27a, 27b) — all need bench or aircraft time.
2. OPEN design items 7, 23, 26-29, 31 — each has a reason; none is a silent gap.
3. The traceability-driven gaps 32b, 42-47 — six requirement rows (FR-032, FR-014, FR-073,
   FR-004/007/015, FR-093, NFR-002/003/004/008) that the code does not currently meet. None
   needs hardware to start; 43 and 46 need a measurement first.
4. UI items from `UI_UX_REVIEW.md` that this cycle did not reach.
5. A SITL pass, so that the mission-protocol code paths (`upload.py`, read-back
   diffing) are exercised by something other than the simulator.

## Cross-reference: audit finding → this list

The four review documents number their findings independently. This table is the join, so
a finding recorded in one document can be tracked in the other.

| Audit ID | Subject | Row here |
|---|---|---|
| `C-1` | Accepted command never reached a terminal state | 1 |
| `C-2` | Independent topology dropped all inbound traffic | 2 |
| `C-3` | Transmitted release frame recycled as "still aboard" | 3 |
| `C-4` | Release permitted during emergency recall | 4 |
| `H-1` | Video start/stop not mutually exclusive; orphan ffmpeg held the device | 11 |
| `H-2` | Zero-frame feed reported `LIVE` and passed the preflight gate | 9 |
| `H-3` | Recording opened on a frameless feed; evidence row never closed | 10 |
| `H-4` | Blocking work on the event loop in mission handlers | 8 |
| `H-5` | Command lifecycle invisible to the operator | Priority 6 summary (lifecycle panel) |
| `H-6` | Onboard failsafe layer only partly provisioned; the GCS sends no `HEARTBEAT` | **27a — OPEN, deliberately** |
| `H-7` | Emergency recall is single-shot | **27b — OPEN, deliberately** |
| `M-1` / `ML-2` | `MAV_CMD_DO_SET_SERVO` has no state expectation | 23 |
| `M-2` / `ML` retry policy | Commands are single-shot, `RETRYING` never set | 26 |
| `M-3` / `ML-4` | Transactions not persisted across a restart | 27 |
| `M-4` / `ML-1` | Freshness refreshed by any message | 29 |
| `M-5` | Abort during transit leaves the survivor unserved | 28 |
| `M-6` | Log screen is buffer-only | 31 |
| `M-7` | Replay synthesises live-shaped vehicle objects | **27c — FIXED** (replay map declares `RECORDED`) |
| `M-8` / `ML-3` | `COMMAND_ACK` matched without transaction correlation | 26a |
| `M-9` | MJPEG keeps serving the last frame after signal loss | **27d — OPEN** |
| `M-10` | Threading regression introduced by the `H-4` fix | 8a |

### Rows added by this reconciliation

| # | Item | Where | Status |
|---|---|---|---|
| 26a | `COMMAND_ACK` is matched on callsign plus command number, not on parameters or a monotonic sequence, so two in-flight transactions of the same command resolve last-first | `telemetry.py` `_on_command_ack` | OPEN — harmless while `attempts` stays 1 (26); becomes a real hazard the moment retries exist, so it is sequenced with 26 rather than patched underneath it |
| 27a | The autopilot-side protection layer is only partly provisioned: the parameter plan writes fence, `RTL_ALT`, `FS_THR_ENABLE` and battery *actions* but not `FS_GCS_ENABLE`, `SYSID_MYGCS`, `FS_EKF_ACTION`, `BATT_LOW_VOLT`, `BATT_CRT_VOLT` — and the station never transmits a MAVLink `HEARTBEAT`, so there is no GCS link for a comm-loss timer to watch | `upload.py` `_parameter_plan`, absent `heartbeat_send` | **OPEN — deliberately.** Arming `FS_GCS_ENABLE` before the station heartbeats would make link-loss *more* dangerous, not less, and picking voltages for an unmeasured pack is inventing hardware behaviour. Sequence: heartbeat sender → parameters → read-back gate in preflight. Until then every document labels comm-loss failsafe `DESIGNED, NOT PROVISIONED` |
| 27b | Recall sends `NAV_RETURN_TO_LAUNCH` once per vehicle per press; the design requires repetition until each heartbeat shows RTL | `mission_engine.py` `emergency_recall` | **OPEN — deliberately.** A repeat loop is a retry policy, and the retry policy (26) is not designed yet; bolting a re-fire onto the release path without one risks a duplicate RTL command arriving after the aircraft already committed. Recall *is* latched before transmitting, so the release gate closes on the first press and the defect is confined to command delivery. Needs a bench decision on re-issue interval and on whether repetition stops on `RTL` heartbeat or on timeout |
| 27c | The replay map built vehicle objects shaped like live telemetry, which is how a recorded sortie comes to look like a live one | `ReplayScreen.tsx` | FIXED — the map's chip is driven by the passed quality (`RECORDED`), and samples carry their persisted `data_quality`, so a simulated sortie replays as `SIMULATED` |
| 27d | The MJPEG relay keeps serving the last decoded frame after the feed goes silent, so an operator watching a frozen image cannot tell freeze from loss | `video.py` stream path | **OPEN.** Freezing rather than blanking is a defensible choice for an analog link mid-dropout; the honest next step is a burned-in `NO SIGNAL` overlay stamped with the last frame's age, which needs the video review's `latency_basis` work before it can be trusted |

Do not read this list as a station that is flight-ready. The readiness gate, the
protected config keys and the refusal to address an unseen aircraft are what stop this
build being flown on paper alone.
