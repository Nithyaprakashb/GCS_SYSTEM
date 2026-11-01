# Video System Review — RescueSwarm GCS (TX800 / TX1500 ground side)

Independent review of the analog video subsystem: capture, health, display, recording and
evidence integrity.
Reviewer: Qoder (Principal Architect / UAV GCS pass). Date: 2026-10-31.

Evidence labels used below: `BUILT` = the code does it and a test drives it. `SIMULATED` =
verified only against the synthetic feed. `PHYSICALLY TESTED` = **nothing in this document
carries this label.** No TX800 transmitter, no RX1500 receiver, no USB capture card and no
analog camera were available during this review.

---

## 1. Scope

| Area | File | Reviewed |
|---|---|---|
| Feed model, capture loops, health, recording | `backend/rescueswarm/video.py` | 866 lines, full read |
| Session evidence rows | `backend/rescueswarm/db.py` (`video_sessions`) | full |
| Endpoints | `backend/rescueswarm/api.py:271-330` | health/start/stop/inject/frame/stream |
| Readiness gate | `backend/rescueswarm/preflight.py:246+` (`fleet.video.*`) | full |
| Operator surface | `desktop/components/VideoPanel.tsx`, `screens/MissionScreen.tsx`, `screens/ReplayScreen.tsx` | full |
| Tests | `backend/tests/test_video_capture.py` | 13 tests |
| Config | `config/fleet.json` → `video.*`, declared in `config.py` | full |

---

## 2. Architectural verdict: the video path is correctly isolated from flight

The audit asked specifically whether the TX800 video chain and the RFD868x telemetry chain are
fully independent subsystems. They are, and the independence is structural rather than a
convention:

- No MAVLink message handler writes a `VideoFeed` field. `_on_mavlink`
  (`telemetry.py:337-437`) touches only `self.vehicles`.
- No video code opens, reads or writes a MAVLink transport. `video.py` imports `telemetry`
  only to *read* cached vehicle state for drawing the synthetic overlay
  (`_sim_loop` → `self.telemetry.vehicle(callsign)`, `video.py:311`) — a one-way,
  read-only, display-only dependency.
- Different processes and different I/O: video runs `ffmpeg` subprocesses with pipes
  (`video.py:500-511`, `733-744`); telemetry runs `pymavlink` connections with reader
  threads (`mavlink.py:55-116`).
- Different failure vocabularies that never cross: `NO_SIGNAL` / `FROZEN` / `BLACK` /
  `UNMAPPED` / `CAPTURE_ERROR` for video, versus `stale` / `TIMEOUT` / `ACK_UNVERIFIED` for
  commands.

Consequences the operator can rely on: losing all three video feeds cannot affect the command
channel, and a MAVLink link loss cannot fabricate or blank a recorded video frame. The only
intended coupling is the readiness gate (`video.required_for_readiness`), which is a
one-directional *permission to fly*, not a data path.

The corresponding limitation is equally structural: because video never touches the command
channel, **the video feed can never be evidence about a flight command.** It is evidence about
what the camera saw. That is why payload release is confirmed by beam evidence and not by
"the kit looks gone on screen" (see `MAVLINK_COMMUNICATION_REVIEW.md` §4).

---

## 3. Capture is commissioned, never guessed

`_start_locked` in REAL mode (`video.py:192-221`) will only start a capture thread for a feed
whose `device_fingerprint` exactly matches an enumerated DirectShow device name. Otherwise the
feed is `UNMAPPED` with the message `"No commissioned capture-device mapping"`. The shipped
profile has `"device_fingerprint": null` for all three feeds, so a REAL start today produces
three `UNMAPPED` feeds and a failing preflight line — which is the correct result, not a bug.

Device contention is handled explicitly (`video.py:199-205`): a second feed configured with
the same device is refused with `"Capture device is already claimed by another feed"`, because
a DirectShow source is exclusive and a silent second opener would steal the live view from the
aircraft that already owns it. Covered by `test_a_capture_device_is_only_ever_claimed_once`.

Recording does **not** open the device a second time. In REAL, `start_recording` pipes the
MJPEG frames the running capture loop already produced into an `image2pipe` ffmpeg encoder
(`video.py:694-707`, `731-744`), and the receipt says so:
`"recorded_from": "CAPTURE_STREAM"`, `"pending_test": true`, and the note *"the analog link
itself is not recorded."* Covered by
`test_real_recording_encodes_the_capture_stream_not_a_second_device_open`.

`stop()` (`video.py:227-292`) terminates every child process with a 2 s wait then a kill,
joins threads, closes every recorder, clears `self.latest`, clears injected faults and resets
every per-feed counter. The comment names the real-world reason this matters: a GCS killed
while ffmpeg still holds the DirectShow device leaves the camera unusable until the station is
rebooted.

---

## 4. Status honesty — the defect class this pass removed

Video's dangerous failure mode is not a black screen; it is a **healthy-looking status with no
picture**. Four defects of exactly that class were found and fixed. All four now have tests.

### 4.1 A started-but-silent feed used to be indistinguishable from a fresh one

The stall detector needed a first frame to age against (`last_frame_mono`). A pipeline that
never produced one had no age, so it sat at `NO_SIGNAL` "starting" forever.

Fix: `VideoFeed.started_mono` (`video.py:74-77`) is stamped at capture-thread launch in both
modes (`:185`, `:211`), and `_health_loop` gained a second branch (`:592-614`) that ages the
zero-frame case against `video.no_frame_timeout_s` (profile 8.0 s), writes
`"No frame N s after capture start"` and publishes one `video.no_frame` warning per session
(`no_frame_event`, `:78-79`) rather than one per poll. `to_dict` exposes `no_frame_s`
(`:105-108`) and `health()` exposes `waiting_count` (`:629-631`), so the console can say
"2 live, 1 dead on arrival" instead of a single misleading count.

Tests: `test_a_started_but_silent_feed_is_never_reported_live`,
`test_a_feed_that_produced_frames_has_no_waiting_clock`.

### 4.2 An empty recording used to count as evidence

`start_recording` accepted a feed with zero frames, produced an empty `.mp4`, and an empty file
in the evidence folder reads as "video was captured" to whoever assembles the sortie report.

Fix: `_recording_blocker()` (`video.py:714-729`) — mapping and pipeline answers first in REAL
because they say what to fix, then `if feed.frames_received == 0: return "Feed has never
delivered a frame; there is nothing to record"`.

Test: `test_recording_refuses_a_feed_that_never_delivered_a_frame`.

### 4.3 A recorder row could stay open forever

`_record_frame` drops a broken recorder from `_recorders` on `BrokenPipeError` (`:776-777`),
which left its `video_sessions` row with a start time and no end — the database claimed a
recording was still running after the sortie.

Fix: `stop_recording` iterates `sorted(set(self._recorders) | set(self._session_rows))`
(`:788-792`), treats a missing writer as `closed = False`, and closes the row through
`db.close_video_session(..., frames_recorded, complete=closed and frames_written > 0, health=…)`
(`:820-827`). `video_sessions` gained `mode`, `frames_recorded` and `complete` columns, with
`_migrate()` adding them to existing databases so old evidence files still open. A recorder
that exits non-zero publishes `video.recording_incomplete` — *"Recorder exited non-zero; the
file may be truncated"* (`:832-839`).

Tests: `test_a_closed_recording_stamps_its_session_row`,
`test_a_recorder_that_died_mid_flight_is_closed_as_incomplete`.

### 4.4 Video lifecycle calls could block abort and recall

`/video/start` and `/video/stop` were synchronous on the event loop while spawning or joining
subprocesses; `/video/stop` and station shutdown ran on the loop while `/video/start` ran in a
worker thread, so a capture spawned just after the other call cleared the table was never
terminated — an orphan holding a DirectShow device and writing to a recorder mid-sortie.

Fix: one `_lifecycle_lock` shared by `start` and `stop` (`video.py:165-171`, `227-235`), and
`video_stop`, `start_mission`, `recall`, `recall_clear` and `mission_reset`'s session reset all
go through `asyncio.to_thread` in `api.py` so recorder spawning cannot stall the abort button.
Tests: `test_stop_clears_injected_faults_and_running_state`,
`test_switching_modes_rebuilds_the_feed_table`.

### 4.5 Latency is reported, never invented

Glass-to-glass delay on an analog link needs a timing pattern and a camera on the ground. That
has never been done here, so the code refuses to guess:

- synthetic feeds: `latency_estimate_ms = None`, `latency_basis = "SYNTHETIC_FEED_NO_LINK"`
  (`video.py:341-345`), covered by `test_simulated_feeds_report_no_latency_number`;
- default for any feed: `"NOT_MEASURED"` (`:70-73`);
- `"MEASURED"` is only reachable if something actually timestamps a frame end-to-end.

### 4.6 Freeze reporting was made honest in simulation too

The injected-freeze branch used to overwrite a genuine fault message with a generic one. It now
says `LIVE`/`Synthetic optical feed` until the freeze has actually lasted past the threshold
and only then `FROZEN`/`Injected frozen frame` (`video.py:320-330`) — the same rule the REAL
health loop applies, so the two modes cannot disagree about when a freeze is a fault.

### Frame-quality detection that was already correct

`_ffmpeg_loop` (`video.py:511-548`) parses JPEG SOI/EOI markers out of the byte stream, keeps a
rolling tail so a split frame is not lost, detects duplicate frames by hash (which is what makes
`FROZEN` mean "same picture", not "no packets"), computes luma to set `BLACK` against
`video.black_luma_threshold` (profile 12.0), and counts a frame that will not decode as
`dropped` rather than silently skipping it — *"A frame that will not decode is a lost frame, not
a silent one."* Two counters are deliberately separate: cumulative `frames_received` and
windowed `window_frames`, because reusing one field made the fps estimate reset the total every
health pass (`video.py:65-68`).

---

## 5. Front-end surface

`VideoPanel.tsx` now shows `AGE no frame yet` when `no_frame_s != null` and a
`silent Ns since capture start` cue, so the zero-frame state is visible rather than inferred.
`MissionScreen.tsx` counts live feeds as `live_count/total` and the recording badge against the
total, so three `NO_SIGNAL` feeds cannot render as three green pips.
`statusTone` in `lib/format.ts` maps `NO_SIGNAL`/`FROZEN`/`BLACK` to warn and
`UNMAPPED`/`CAPTURE_ERROR` to danger; previously several of these fell through to a neutral tone.
`ReplayScreen.tsx` passes `provenance="RECORDED"` to `TacticalMap`, which renders the chip
"RECORDED — NOT A LIVE LINK"; the live mission view renders "LIVE TELEMETRY" or
"SIMULATED — NOT FLIGHT DATA" from the same prop. TypeScript compiles clean with `strict` and
`noUnusedLocals`.

---

## 6. Findings still open

| ID | Location | Problem | Severity | Status |
|---|---|---|---|---|
| V-1 | `video.py:547` | `_ffmpeg_loop` assigns `feed.status = "LIVE"` on **every** parsed frame, including a duplicate one. A frozen feed is only caught on the next health pass (≤ 0.5 s later), so `LIVE` and `FROZEN` can oscillate when the freeze threshold sits near the polling interval. The window is short and self-correcting, but the status is not derived solely from evidence. | LOW | **Open** — fix would move the status decision entirely into `_health_loop` |
| V-2 | `video.py:763-786`, `_record_frame` | The `cv2.VideoWriter` exception path (`:785-786`) drops a frame silently — it does not increment `feed.dropped`, unlike the MJPEG decode path. A failing writer looks like normal operation apart from the final `frames_recorded` count. | LOW | **Open** |
| V-3 | `video.py:858-866`, `mjpeg_stream` | The generator stops emitting when `latest[callsign]` does not change. Mid-session signal loss leaves the browser holding the **last picture** with no visual loss-of-signal card; the status text is correct but the image pane is not. `self.latest` is cleared only on `stop()`. | MEDIUM | **Open** — recommend a synthetic "NO SIGNAL" frame published by the stream when `status not in {LIVE, RECORDING, FROZEN}` past the timeout |
| V-4 | `video.py:498-556` | No capture-frame-rate assertion. The pipeline requests `-r 10` and `REAL_RECORD_FPS = 10.0`, but nothing verifies the device delivers it; a receiver feeding 3 fps is recorded as 10 fps wall-clock-stretched by ffmpeg's default timing. | MEDIUM | **Open** — needs a real device to characterise; `PHYSICALLY TESTED` gap |
| V-5 | `video.py:482-496`, `discover_capture_devices` | Device matching is by human-readable DirectShow name, which is not stable across USB port changes or driver revisions. `device_fingerprint` is documented as commissioned, so this is a robustness note, not a safety hole. | LOW | **Open** — recommend recording VID/PID/serial alongside the name at commissioning |
| V-6 | `video.py:866` | `mjpeg_stream` polls at a fixed 0.08 s; there is no backpressure, so a client that stops reading blocks the generator's thread. Acceptable for a single-operator LAN-bound desktop app. | LOW | **Open** |
| V-7 | `video.py:682-690` (SIM only) | The simulated `cv2.VideoWriter` emits `[mpeg4] Invalid pts (N) <= last (N)` for duplicate timestamps. Artefact is confined to `SIMULATED` evidence files; REAL uses the stdin pipe path and does not hit it. | LOW | **Open**, documented in `docs/KNOWN_ISSUES.md` as part of the final doc pass |

None of V-1…V-7 is a safety-critical defect. V-3 and V-4 are the two that affect what an
operator can actually see and what the recorded file is worth.

---

## 7. Test evidence

`backend/tests/test_video_capture.py` — **13 tests, all passing** in the run recorded in
`docs/audit/TEST_RESULTS.md` (whole suite: 93 passed). Six of the thirteen are new from this
review and each one failed against the pre-fix code: §4.1 two, §4.2 one, §4.3 two, plus the
corrected pre-existing test whose assertion had pinned the old behaviour.

Revert experiment for this cluster: with the video fixes removed, **4 failed / 9 passed**;
restored, **13 passed**. That is the evidence that the tests bind the fixes and are not
tautologies.

`tools/sim_e2e.py` full-sortie mode exercised the simulated capture path end-to-end
(`delivered=10`, terminal phase `COMPLETE`), which proves the recording lifecycle, session
close-out and health loop run under load. It proves nothing about a real capture device.

---

## 8. What this review does **not** establish

- That the TX800 → RX1500 → capture-card chain works at any range, or that three simultaneous
  5.8 GHz links coexist. Untested; no hardware.
- That `ffmpeg -f dshow` finds the specific capture device on the station PC, or the latency
  and CPU cost of three concurrent decode pipelines at 10 fps.
- That the recorded `.mp4` files are playable in the tools a judge or investigator will use
  (`libx264`/`yuv420p` is a reasonable choice, not a verified one).
- That `black_luma_threshold: 12.0` or `freeze_timeout_s: 2.0` are the right values for real
  video; both are engineering guesses that need bench tuning.
- That video recording is continuous from mission start to mission end. It is started and
  stopped by explicit calls; the gap around those calls is unmeasured.
- Any performance claim. The words "LIVE" and "RECORDING" describe what the code believes about
  its own pipeline, and after this pass they describe it honestly — they are not measurements of
  the radio link.
