# RescueSwarm GCS — Independent Audit

Auditor role: principal software architect / UAV GCS engineer / MAVLink integrator /
QA lead / human-factors reviewer, working against an existing codebase. This is not a
greenfield design document; every finding below is a statement about code that is
present in this repository at the time of writing.

Repo reviewed: `C:\Users\nithy\Desktop\GCS SYSTEM` (backend `backend/rescueswarm/`,
tests `backend/tests/`, frontend `desktop/src/`, profile `config/fleet.json`).

---

## 1. Method and limits

What was done:

1. Read the backend modules end to end (`telemetry.py`, `delivery.py`, `ledger.py`,
   `safety.py`, `mission_engine.py`, `upload.py`, `preflight.py`, `video.py`,
   `survivors.py`, `allocation.py`, `db.py`, `api.py`, `simulation.py`, `state.py`).
2. Read the frontend screens and shared primitives, and compared every state name the
   UI renders against the state names the backend actually emits.
3. Executed the pytest suite, `tsc --noEmit`, `vite build` and the end-to-end
   simulated sortie (`tools/sim_e2e.py`) in both completion and abort modes.
4. For each headline finding, wrote a test first, confirmed the test failed against the
   unfixed code, then fixed the code and confirmed it passed.

What was not done, and therefore cannot be claimed here:

- No physical aircraft, no RFD868x modem, no TX800 video receiver, no Pixhawk, no
  servo latch was ever connected to this station. Nothing in this document is a
  statement about flight behaviour.
- No screenshot of the running interface was captured: pixel capture of the in-app
  browser was unavailable in this environment. UI findings are static code-and-style
  review. See `UI_UX_REVIEW.md`, section "scope".
- SITL was not run for this review; `docs/SITL.md` describes how, and every SITL result
  remains PENDING TEST.

Evidence labels used throughout: `DESIGNED` (documented intent only), `CALCULATED`
(derived from numbers in the repo), `ASSUMED` (plausible, unproven), `BUILT` (code
exists and executes), `SIMULATED` (proven only against the simulator),
`PHYSICALLY TESTED` (proven on hardware — none of the findings below carry this label),
`PENDING TEST` (implemented, not yet executed).

---

## 2. Defect register

Format: `file | function/component | problem | severity | evidence | correction`.
Status is either **FIXED** (with the regression test that pins it) or **OPEN**.

### 2.1 CRITICAL

**C-1 — an accepted command could never reach a terminal state, so a release never settled**
`backend/rescueswarm/telemetry.py` | `TERMINAL_TX_STATES`, `_sweep_transactions`,
`_settle` | A transaction that received `COMMAND_ACK` with `ACCEPTED` was moved to
`ACKNOWLEDGED` and `ACKNOWLEDGED` was a member of `TERMINAL_TX_STATES`. The sweep
skips terminal transactions, so the state-verification deadline that
`send_command` had armed (`tx.verify_deadline`) was never examined. Every accepted
command froze in `ACKNOWLEDGED`; the delivery engine's settle callback fired only for
terminal states, so a payload release that the autopilot had accepted stayed in
`ACK_PENDING` forever and the kit never reached `RELEASE_SENT`. | CRITICAL | The
sweep's own guard (`if tx.state in TERMINAL_TX_STATES: continue`) contradicted the
verification deadline it had just set; `delivery.settle_release_transaction` is called
from `_settle` only. | Split the two ideas: `TERMINAL_TX_STATES` no longer contains
`ACKNOWLEDGED`; a new `SETTLING_TX_STATES = TERMINAL_TX_STATES | {"ACKNOWLEDGED"}`
drives the notify-and-settle path so the delivery engine still reacts the instant an
ACK arrives, while the sweep keeps watching the verification deadline. When the
deadline passes with no vehicle state, the transaction becomes `ACK_UNVERIFIED`,
never silently `ACKNOWLEDGED`. | **FIXED** — `test_acknowledged_is_not_terminal_and_still_settles_the_release_engine`,
`test_accepted_command_becomes_verified_only_when_vehicle_state_confirms`,
`test_accepted_command_becomes_ack_unverified_when_the_state_never_arrives`,
`test_command_without_a_state_check_stays_acknowledged_not_verified`.

**C-2 — independent (per-vehicle) telemetry topology dropped every inbound message**
`backend/rescueswarm/telemetry.py` | `_on_mavlink` endpoint-routing | On an
independent link each modem presents its own endpoint, so messages arrive with that
endpoint's vehicle as source. A blanket "if the message belongs to an endpoint,
handle only heartbeats and return" discarded position, statustext, mission and
parameter replies. In independent mode the GCS showed a link it could not see inside:
heartbeats updated, everything else vanished. | CRITICAL | The early `return` sat
before the `msg_type` dispatch table; `_address_block` then refused to command
aircraft whose identity was never confirmed by non-heartbeat traffic. | The blanket
drop was removed; routing is done by the per-message source check, which still
rejects a message whose `sysid` does not match the endpoint's commissioned aircraft. |
**FIXED** — `test_independent_modem_still_processes_its_own_aircraft`,
`test_independent_modem_still_rejects_the_wrong_aircraft`.

**C-3 — a release frame that left the station could be counted back in as "still aboard"**
`backend/rescueswarm/delivery.py` `settle_release_transaction`,
`backend/rescueswarm/ledger.py` `note_command_abandoned`,
`backend/rescueswarm/mission_engine.py` `_reconcile_kits_on_abort` | When a release
command timed out or was abandoned after being written to the transport, the code
treated the kit as un-flown: `note_command_abandoned` accepted only
`load_status == "RELEASE_SENT"`, the timeout path returned the kit to `LOADED`, and
the abort reconciliation cancelled the reservation. A servo latch pulse that went out
over an un-ACKed link therefore re-entered the magazine, and the same physical kit
could be promised to a second survivor while already falling out of the first
aircraft. | CRITICAL | `CommandTransaction` had no record of whether a frame was
actually written; the only signal available to the ledger was "no ACK", which is not
evidence of "no pulse". | Added `transmitted: bool` to the transaction, set only after
`transport.command_long(...)` returns (simulation never sets it). The ledger now
accepts the abandon transition from `RESERVED` as well; `settle_release_transaction`
routes a transmitted-but-unproven frame to `UNKNOWN` with `beam_result=NO_EVIDENCE`,
and `_reconcile_kits_on_abort` marks the same case `UNKNOWN` instead of returning it
to the magazine. A kit whose fate is unknown is never re-fired and never re-reserved. |
**FIXED** — `test_release_frame_that_left_the_station_is_never_counted_back_in`,
`test_abandoned_reservation_is_marked_unknown_instead_of_silently_kept`,
`test_abort_answers_with_a_body_and_leaves_no_kit_reserved`.

**C-4 — payload release was not blocked while an emergency recall was active**
`backend/rescueswarm/safety.py` | `release_clearance` | `recall_active` was
maintained, published and rendered, but the release gate never consulted it. With the
fleet commanded home, a delivery task already sitting at `HOVER_VERIFY` with a kit
reserved could still be given a servo frame. | CRITICAL | The clearance check list had
`kit_reservation`, `hover_verified`, `battery`, `geofence` and `separation`, and no
recall entry; `request_release(hover_verified=True)` was reachable from the delivery
engine without any recall test. | Added a `recall_active` clearance entry that fails
closed and states the reason; the operator sees "Emergency recall is active: payload
release is blocked while the fleet is returning". | **FIXED** —
`test_recall_blocks_a_payload_release`.

### 2.2 HIGH

**H-1 — video start/stop were not mutually exclusive; an orphan ffmpeg kept the capture device**
`backend/rescueswarm/video.py` | `start`, `stop` | `/video/start` runs in a worker
thread (`asyncio.to_thread`) while `/video/stop` and station shutdown ran on the
event loop. A capture process spawned just after the other call cleared the
`_processes` table was never terminated. On Windows a DirectShow source is
exclusive, so an orphan made the receiver unusable until the station was rebooted,
and an orphan recorder kept appending frames after the sortie. | HIGH | `start()`
called `stop()` internally with no lock; the plain `self._lock` is an RLock used for
frame data, so nesting hid the race rather than preventing it. | A dedicated
`_lifecycle_lock` (plain `Lock`, deliberately non-reentrant) now serialises
`_start_locked`/`_stop_locked`; `/video/stop` moved onto `asyncio.to_thread` so
waiting on that lock cannot stall the API. | **FIXED** —
`test_a_capture_device_is_only_ever_claimed_once`,
`test_unmapped_feeds_never_start_a_capture_process`,
`test_switching_modes_rebuilds_the_feed_table`.

**H-2 — a feed was reported LIVE before a single frame existed, and a zero-frame feed looked healthy forever**
`backend/rescueswarm/video.py` | `start` (SIMULATION branch), `_health_loop`,
`preflight.py:246-264` | Synthetic feeds were marked `LIVE` at thread creation.
Preflight's `fleet.video` check passes on `status == "LIVE"`, so a stalled sim loop, or
a REAL capture process that exited immediately, could satisfy the readiness gate
without ever producing a frame. The stall detector is guarded by
`if feed.last_frame_mono:` — a feed with no first frame has no age, so it never
became `FROZEN` and never reported anything. | HIGH | Reading the two branches
together: status assignment before the loop's first iteration, and the stall
condition that cannot be evaluated when `last_frame_mono == 0.0`. | Feeds now start
`NO_SIGNAL` ("Synthetic feed starting; no frame yet") and become `LIVE` only when a
frame is put on screen. `VideoFeed.started_mono` gives the zero-frame case a clock;
`_health_loop` reports `No frame N s after capture start` after
`video.no_frame_timeout_s` (new declared setting, default 8 s) and publishes one
`video.no_frame` warning per session. `health()` gained `waiting_count`, and each feed
now carries `no_frame_s`, which the console renders as "no frame yet" plus a silent-for
cue. | **FIXED** — `test_a_started_but_silent_feed_is_never_reported_live`,
`test_a_feed_that_produced_frames_has_no_waiting_clock`,
`test_simulated_feeds_report_no_latency_number`.

**H-3 — recording accepted a feed that had never delivered a frame, and the evidence row was never closed**
`backend/rescueswarm/video.py` | `start_recording`, `stop_recording`,
`db.record_video_session` | REAL recording checked only that the capture thread was
alive. With a locked-but-silent receiver it opened an ffmpeg writer, produced an empty
or near-empty `.mp4`, filed it in the evidence folder and inserted a `video_sessions`
row. That row was inserted with `end_time = NULL` and never updated, so nothing could
later distinguish a finished recording from a session whose recorder died. | HIGH |
`test_video_capture.py` previously *asserted* this behaviour
(`assert results["INS-01"]["recording"] is True` together with
`assert frames_before == 0`), which is how it survived review: the test pinned the bug. |
`_recording_blocker()` now returns, in the order the operator can act on: not mapped →
pipeline not running → "Feed has never delivered a frame; there is nothing to record".
`record_video_session` returns its row id and a new `close_video_session` stamps
`end_time`, `frames_recorded`, `complete` and a final health snapshot; both the
normal stop path and the dropped-pipe path close the row, with `complete = 0` when the
recorder exited non-zero or wrote nothing. Schema migration adds
`mode`, `frames_recorded`, `complete` to older databases. The test that pinned the old
behaviour was corrected. | **FIXED** —
`test_recording_refuses_a_feed_that_never_delivered_a_frame`,
`test_a_closed_recording_stamps_its_session_row`,
`test_a_recorder_that_died_mid_flight_is_closed_as_incomplete`.

**H-4 — mission-critical HTTP handlers ran blocking work on the event loop**
`backend/rescueswarm/api.py` | `start_mission`, `recall`, `recall_clear`,
`mission_reset`, `video_stop` | `POST /mission/start` calls
`video.start_recording`, which spawns ffmpeg per feed, plus SQLite commits, directly
on the loop. While that ran, no other request could be served — including
`/mission/abort` and `/mission/recall`, which share the loop. | HIGH | The sibling
endpoints (`abort`, `gate`, `preflight`, `upload`, `replay`) already used
`asyncio.to_thread`; these five did not. | All five now dispatch through
`asyncio.to_thread`. | **FIXED** (behaviour verified by the suite and the e2e run;
event-loop fairness itself is `BUILT`, not load-tested).

**H-5 — the command lifecycle was invisible to the operator**
`desktop/src/screens/MissionScreen.tsx` | phase rail, transaction panel | The UI had
no rendering of `state.fleet.transactions`; the operator could not see SENT vs
ACKNOWLEDGED vs VERIFIED at all. The phase rail also used a hand-written list that
omitted `ACK_PENDING`, `CONFIRMED`, `UNKNOWN`, `ABORTED` and put `HOLD_CLEARANCE`
after `TRANSIT`, so a confirmed delivery rendered as an unfinished one. | HIGH |
Backend emits `transactions` in the fleet snapshot (`telemetry.snapshot`); the type was
not even declared in `desktop/src/lib/types.ts`. | Added the `CommandTransaction`
type, `DELIVERY_SPINE` matching `delivery.py` exactly, a `TxRail` showing
sent → acknowledged → state-verified, and a Command lifecycle panel whose legend states
that an ACK is the autopilot answering, not proof that the aircraft did it.
`txTone` renders `ACKNOWLEDGED` in warning amber, never success green. |
**FIXED** — `BUILT` and typechecked; per-screen judgement is in `UI_UX_REVIEW.md`.

**H-6 — the onboard failsafe layer the design promises is only partly provisioned**
`backend/rescueswarm/upload.py` | `_parameter_plan` (lines 525-547) | The design document
(`docs/reference/09_FAIL_SAFE_FEATURES_AND_SAFETY.md` §2) states that *"the GCS writes these
parameters to each drone during setup and reads them back"*, and lists comm-loss, EKF and
battery-threshold fail-safes as required. The parameter plan the GCS actually writes is
`FENCE_ENABLE/FENCE_TYPE/FENCE_ACTION/FENCE_ALT_MAX`, `RTL_ALT`, `FS_THR_ENABLE`,
`BATT_FS_LOW_ACT`, `BATT_FS_CRT_ACT`, `WPNAV_SPEED`. Missing: `FS_GCS_ENABLE` and
`SYSID_MYGCS`, `FS_EKF_ACTION`, `BATT_LOW_VOLT` and `BATT_CRT_VOLT` — and the battery
*actions* are armed while their thresholds are left at whatever the vehicle holds. Worse, the
GCS never transmits a MAVLink `HEARTBEAT` at all (no `heartbeat_send` anywhere in
`backend/`, and `SYSID_MYGCS` appears only in `docs/reference/`), so there is no GCS link for
the autopilot's comm-loss timer to observe. Layer 2 therefore does not do what Layer 3's
documentation says it does, and a lost radio silently removes a protection the operator
believes is armed. | HIGH | grep of `backend/` + `desktop/src/` for each parameter name;
`config/fleet.json` carries `battery_low_pct` / `battery_critical_pct` for all three vehicles
and **no code reads them**. Simulation cannot see this because the simulated aircraft does not
read autopilot parameters. | Not fixed here: choosing voltages for a 6S 22 Ah pack without a
measured discharge curve, or arming `FS_GCS_ENABLE` before the GCS actually heartbeats, would
be inventing hardware behaviour. Required work, in order — (1) implement a GCS heartbeat
sender on each transport at the `sysid` the vehicles expect; (2) extend `_parameter_plan` with
`FS_GCS_ENABLE`, `SYSID_MYGCS`, `FS_EKF_ACTION`, `BATT_LOW_VOLT`, `BATT_CRT_VOLT`,
`FS_GCS_TIMEOUT`; (3) bind the per-vehicle `battery_*_pct` profile values to something or
delete them; (4) preflight-fail if any of those read back wrong. Until (1)-(4) land, the docs
must say the comm-loss failsafe is `DESIGNED, NOT PROVISIONED`. | **OPEN — `PENDING TEST`**;
the `FENCE_TYPE` circle-bit deferral at `upload.py:528-532` is *not* part of this finding, it
is deliberate and correct.

**H-7 — EMERGENCY RECALL is single-shot where the design requires repetition**
`backend/rescueswarm/mission_engine.py` | `emergency_recall` (lines 541-556) | The spec
(`docs/reference/09` §2 row 6) says recall *"is sent repeatedly until each heartbeat shows
RTL"*. The implementation sends `NAV_RETURN_TO_LAUNCH` once per vehicle per press and relies
on the transaction lifecycle to report `ACK_UNVERIFIED` if RTL never engaged. A single lost
RTL frame — one dropped packet on an 868 kHz multipoint link during the exact moment the
operator is most alarmed — leaves an aircraft in GUIDED with no further ground attempt.
| HIGH | `RETRYING` is never set (`M-2`); `send_command` fixes `attempts = 1`
(`telemetry.py:618`); nothing in `mission_engine` re-issues RTL for a vehicle whose mode is
not RTL/SMART_RTL/CIRCLE. Recall *is* correctly latched (`safety.set_recall` closes the
release gate first, before transmitting), so the defect is limited to command delivery, not to
the ground-side interlock. | Needs a per-vehicle recall retry loop driven from the mission
monitor: while recall is active and `custom_mode ∉ {6, 21, 7}` and the link is addressed,
re-command RTL at a bounded interval with a recorded attempt count. This is command authority
in an emergency path, so it must be written and reviewed deliberately, not bolted on mid-audit.
| **OPEN** — `DESIGNED` in the reference, `BUILT` only as a single shot,
`PHYSICALLY TESTED` never.

### 2.3 MEDIUM

**M-1 — `MAV_CMD_DO_SET_SERVO` (183) has no state expectation — OPEN**
`backend/rescueswarm/telemetry.py` | `_expectation_for` | Expectations exist for
arm/disarm, takeoff, land, RTL, return-to-launch and waypoint commands. There is none
for 183, so a release command can reach `ACKNOWLEDGED` but never `VERIFIED`:
`SERVO_OUTPUT_RAW` is not watched. The physical latch is therefore proven only by beam
evidence, not by telemetry. | MEDIUM | `_expectation_for` covers commands 176, 400,
22, 21, 20, 192. | Honest options: watch `SERVO_OUTPUT_RAW` on the release channel for
the commanded PWM with a short window (needs a real servo output rate to be
meaningful), or keep the current behaviour and document that a release is proven by
beam evidence only. The second is what ships; it is recorded here so it is not
mistaken for a verification gap in the UI. Status: `PENDING TEST` on a bench with a
scope on the servo lead.

**M-2 — commands are single-shot; `attempts` never exceeds 1** — OPEN.
`telemetry.py` `send_command` sets `attempts = 1` and nothing retries; `RETRYING`
is a reachable state in the type but never set by the backend. A lost frame becomes
`TIMEOUT` and the operator must re-command. Acceptable for payload release (a blind
re-fire is worse), wrong for waypoint upload. `MEDIUM`, documented in
`SAFETY_AND_FAILURE_ANALYSIS.md`.

**M-3 — transactions are not persisted across a restart** — OPEN. An in-flight
release whose ACK arrives after the GCS process restarted is unresolvable; the kit is
left as `UNKNOWN` by the reconciliation above, which is the safe answer but loses the
audit trail. `MEDIUM`; the evidence log still holds the command.

**M-4 — freshness is refreshed by any message, not only heartbeats** — OPEN.
`telemetry.py` updates `last_seen_mono` for non-heartbeat traffic too, so an aircraft
that has stopped heartbeating but still emits statustext continues to read as fresh.
For the release gate the relevant fact is that the link carries MAVLink, so this is a
judgement rather than a defect, but it makes `heartbeat_age_s` the number to trust.
`MEDIUM`; now surfaced in the aircraft row ("Link 92% · hb 0.4s").

**M-5 — an abort during transit leaves the survivor unserved and never re-tried** — OPEN.
`mission_engine._reconcile_kits_on_abort` returns an un-flown kit to the magazine and
the survivor to `UNSERVED`, but nothing re-allocates it in the same sortie; the
operator must request it explicitly (`allocator.retry_unserved` is reachable from the
survivor panel only in specific phases). `MEDIUM` workflow gap, documented rather than
silently patched, because automatic re-tasking after an abort is a decision the
operator should own.

**M-6 — `LogsScreen` is buffer-only** — OPEN. The log screen reads the in-memory ring
(`bus.recent`), so events older than the buffer are not browsable there; the durable
record is `/api/missions/{id}/events`. `MEDIUM`, documentation issue in the first
instance.

**M-7 — `ReplayScreen` synthesises live-shaped vehicle objects** — partially fixed.
`emptyVehicle()` sets `connected: true`, `identity_valid: true`, `ekf_ok: true` for
historical rows. Those fields are not what replay claims to show, and the row carries
`data_quality: "RECORDED"` and `stale: true`; the map now also shows
"RECORDED — NOT A LIVE LINK". Remaining risk is a reader treating a marker as a live
link. `MEDIUM`.

**M-8 — `COMMAND_ACK` is matched without transaction correlation** — OPEN.
`telemetry.py:714-715` scans transactions in reverse for the same callsign and command
number, so two in-flight transactions with the same command resolve last-first. The
delivery engine issues one command per vehicle per step, which keeps the exposure small
today; it becomes a real hazard the moment retries exist (`M-2`). `MEDIUM`.

**M-9 — the MJPEG stream keeps serving the last frame after signal loss** — OPEN.
`video.py:858-866` only emits when `latest[callsign]` changes, and the table is cleared on
`stop()`, so a mid-session feed failure leaves the browser holding a stale picture with no
loss-of-signal card. The status text beside it is correct. `MEDIUM`, see
`VIDEO_SYSTEM_REVIEW.md` V-3.

**M-10 — a fix introduced by this review** — FIXED, recorded because it is the kind of
change that normally goes unreported. Moving `POST /mission/start` onto `asyncio.to_thread`
(`H-4`) put `SimulationEngine.start` on a worker thread, where
`asyncio.create_task(self._run())` has no running loop. The end-to-end run caught it —
`RuntimeWarning: coroutine 'SimulationEngine._run' was never awaited`, start refused, phase
stuck at `EXECUTING`, 0 kits delivered after 180 s — and no unit test did. Fixed with
`events.bound_loop()` plus `_schedule_run()` / `loop.call_soon_threadsafe` in
`simulation.py`, and `backend/tests/test_threading.py` now covers exactly that thread
context. Proven by revert experiment: `RuntimeError: no running event loop`, 2 failed → 2
passed. See `PRIORITISED_FIXES.md` item 8a and `TEST_RESULTS.md`.

### 2.4 LOW

- `statusTone` had no entry for `CONFIRMED` and the video states, so a confirmed
  delivery rendered grey like an unknown one. **FIXED** (now in `lib/format.ts`).
- `BootScreen` used a tick glyph, in amber, for a `warn` check — a success shape on an
  unproven check. **FIXED** (`TriangleAlert`).
- The header fleet link dot put its tone class on `.pip` while the CSS only matched
  `.fleet-dot.ok .pip`, so it never changed colour. **FIXED** (`.pip.ok/.warn/.danger`).
- The mission timer label read "cap 30:00" and the counters read "/3" regardless of the
  profile. **FIXED** — `mission.time_cap_min` is now published by
  `mission_engine.status()` and used by the UI; fleet and video counts come from the
  state objects.
- Video panel showed `AGE —` for a feed that had never produced a frame. **FIXED**
  ("AGE no frame yet" plus a silent-for cue).

---

## 3. Subsystem independence: RFD868x telemetry vs TX800 video

Verified as genuinely independent, with two shared dependencies that matter:

- Data paths do not intersect. Telemetry is pymavlink serial endpoints in
  `telemetry.py`/`mavlink.py`; video is FFmpeg capture subprocesses in `video.py`. No
  video code imports or calls the transport; no telemetry code reads a frame.
- Identity never comes from video. Feed-to-aircraft mapping is a commissioned
  `device_fingerprint` in `config/fleet.json`; `_start_locked` refuses to open a
  device a second feed already claimed, and the code states that enumeration order is
  not identity.
- Failure isolation is real in one direction and partial in the other. A dead video
  feed cannot stop telemetry (separate threads, separate processes) and preflight's
  `fleet.video` is a launch gate, not a flight gate. But both run inside one GCS
  process: an uncaught fault in the video health loop, or an ffmpeg child process
  reaping problem, shares the process with command transmission. That is the accepted
  station design (single-operator, single machine) and is stated in
  `SAFETY_AND_FAILURE_ANALYSIS.md` rather than presented as isolation.
- Analog video is never a control channel. Nothing in `delivery.py` or `safety.py`
  consumes a frame, a luma value or a video status string; the beam-confirmation input
  for `CONFIRMED` comes from an event id in the payload ledger, not from video.

---

## 4. Command lifecycle and state machines, as built

Lifecycle: `CREATED` → `SENT` (frame written to a transport) → `ACKNOWLEDGED`
(autopilot answered `ACCEPTED`) → `VERIFIED` (the vehicle state the command asked for
was seen within `state_verify_s`). Failure exits: `REJECTED` (NACK, or refused by the
address gate before any write), `TIMEOUT` (never answered), `ACK_UNVERIFIED`
(answered, but the physical result was never observed). A command with no defined
state check stops at `ACKNOWLEDGED` and is labelled as such — the UI legend says
plainly that an ACK is the autopilot answering, not proof that the aircraft did it.
Label: `BUILT`, exercised only against the simulator (`SIMULATED`); nothing here is
`PHYSICALLY TESTED`.

Delivery machine (`delivery.py`) is the single spine the UI now mirrors. Interruption
paths audited: recall during `HOVER_VERIFY` (blocked at the release gate, C-4);
abort during transit (kit returned, survivor unserved, M-5); abort with a transmitted
release frame (`UNKNOWN`, never recycled, C-3); unanswered release (timeout → kit back
in the magazine only if nothing was written); late duplicate ACK (settles exactly once,
`already_settled`); ACK from a non-autopilot component (ignored).

---

## 5. What this audit explicitly does not claim

- It does not claim the station is flight-ready. `payload_hardware_verified` is false,
  `topology_verified` is false and is API-protected, and REAL mode refuses to address
  an aircraft it cannot see. Every REAL-mode statement in this repo is
  `PENDING TEST`.
- It does not claim any measured RF, latency, battery or drop-accuracy figure. TX800
  glass-to-glass latency has never been measured here; `latency_basis` reports that.
- It does not claim the LLM copilot is part of safety. It is advisory-only, protected
  by config, has no MAVLink or payload import, and `test_engine_has_no_llm_dependency`
  keeps it out of the mission path.
- No test result in this document is asserted without the run that produced it; see
  `TEST_RESULTS.md` for the exact commands, counts and the revert experiment.
