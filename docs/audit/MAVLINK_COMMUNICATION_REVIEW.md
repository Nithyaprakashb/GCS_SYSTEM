# MAVLink & Communication Review — RescueSwarm GCS

Independent review of the telemetry, command and mission-transfer path.
Reviewer: Qoder (Principal Architect / MAVLink integration pass).
Date: 2026-10-31.

Evidence label for **everything in this document**: `DESIGNED` + `BUILT` + `SIMULATED`.
Nothing here is `PHYSICALLY TESTED`. No RFD868x modem, no ArduPilot vehicle and no real
MAVLink stream was present during this review. Every statement about wire behaviour is a
statement about what the code does when a message object is handed to it, verified through
`pymavlink`-shaped message doubles in `backend/tests/` and the deterministic simulation in
`tools/sim_e2e.py`.

---

## 1. Scope and method

Reviewed files:

| File | Role | Lines |
|---|---|---|
| `backend/rescueswarm/mavlink.py` | Transport objects, reader thread, message writers | 259 |
| `backend/rescueswarm/telemetry.py` | Message dispatch, identity, staleness, command transactions | ~900 |
| `backend/rescueswarm/upload.py` | Mission / fence / parameter upload and read-back comparison | 547 |
| `backend/rescueswarm/config.py` | Link settings, protected keys, ranges | — |
| `config/fleet.json` | The shipped profile (all endpoints are `null`) | — |
| `backend/rescueswarm/preflight.py`, `system.py` | Where link status gates the operator | — |

Method: read the dispatch path end-to-end, trace each transaction state transition to the
line that performs it, then check which of those paths the test suite actually exercises.
Two defects found in this pass were fixed during the review (see §7); both were found by
reading, confirmed by a failing test, and closed with a regression test.

---

## 2. The link model is honest about being unverified

`MavlinkTransport.open()` (`mavlink.py:55-88`) does not report success when a port opens.
It calls `wait_heartbeat(timeout=3.0)` and raises `TransportError` if the return is `None`:

```python
heartbeat = self.conn.wait_heartbeat(timeout=3.0)
if heartbeat is None:
    raise TransportError(
        "Serial/network port opened but no MAVLink HEARTBEAT arrived within 3 s"
    )
```

This is the correct rule and it is enforced, not documented: a COM port that enumerates, a
socket that binds, or a modem that powers up is never treated as an aircraft link.
`send()` and `_require_link()` (`mavlink.py:118-162`) both refuse to write unless
`self.conn and self.connected`, so a dropped link cannot produce a "successful" upload the
caller never sees fail.

Two topologies are supported (`telemetry.py:250-293`):

- `shared` — one multipoint transport, endpoint from `telemetry.shared_endpoint`.
- `independent` — one point-to-point transport per callsign, from
  `telemetry.independent_endpoints[callsign]`; a missing endpoint raises rather than
  silently falling back to the shared link.

The GCS source system is configurable (`gcs_sysid`, default 255) and the source component is
fixed at 190. Vehicle targeting uses `state["sysid"]` with target component 1
(`telemetry.py:655`), i.e. the autopilot, never component 0.

**The most important honesty property is `telemetry.topology_verified`.** It is declared
`{"kind": "protected"}` in `config.py:61`, meaning the API rejects a write to it with a
stated reason ("RFD868x topology verification is a physical commissioning record"). The
shipped profile carries `"topology_verified": false`, and after a successful REAL connect the
manager still sets `topology_status = "UNVERIFIED"` and returns
`"status": "Radio topology unverified — commissioning required"` (`telemetry.py:289-293`).
Connecting is not commissioning. `preflight.py:235-245` turns the flag into a
`fleet.radio_topology` FAIL line, which is a readiness gate, so a REAL sortie cannot be
started on an unproven radio topology. `system.py:87` surfaces the same fact as a WARN on the
hardware panel. This is verified by grep across `docs/` — the flag is set only by hand after
bench work, never by code.

Staleness: `heartbeat_timeout_s: 3.0` and `stale_after_s: 2.0` in the profile, both marked
`SAFETY_CRITICAL` with bounded ranges in the schema, so a mistyped config cannot make the
fleet look permanently fresh.

One caveat recorded as a finding, not a fix: freshness is refreshed by **any** MAVLink
message, not only HEARTBEAT (`telemetry.py:370-377` sets `last_seen_mono` and
`connected = True` for every non-heartbeat message). A vehicle that streams position but has
stopped heartbeating still reads as online. `MEDIUM` — see §7.

---

## 3. Command lifecycle as built

The state machine is declared at `telemetry.py:40-54` and each edge is performed by exactly
one site:

```
CREATED → SENT → ACKNOWLEDGED → VERIFIED
                → REJECTED
                → TIMEOUT
                → ACK_UNVERIFIED
```

| State | Where set | Meaning |
|---|---|---|
| `SENT` | `send_command`, `telemetry.py:618` | Transaction registered; not yet on the wire |
| `REJECTED` (NOT_ADDRESSED) | `telemetry.py:640-651` | REAL-mode gate refused the command before writing |
| `REJECTED` (transport) | `telemetry.py:665-669` | The write itself raised |
| `REJECTED` (MAV_RESULT) | `_on_command_ack`, `telemetry.py:729-733` | Autopilot answered non-zero |
| `ACKNOWLEDGED` | `telemetry.py:720-728` | `COMMAND_ACK` with `result == 0` from component 1 |
| `TIMEOUT` | `_sweep_transactions`, `telemetry.py:686-692` | No `COMMAND_ACK` within `command_timeout_s` |
| `VERIFIED` | `_mark_verified`, `telemetry.py:560-567` | Accepted **and** the expected vehicle state appeared |
| `ACK_UNVERIFIED` | `telemetry.py:699-706` | Accepted, expected state never appeared within `state_verify_s` |

`ACKNOWLEDGED` is explicitly **not** proof of the physical action. Two mechanisms enforce
that rather than just asserting it:

1. `TERMINAL_TX_STATES = {"VERIFIED", "REJECTED", "TIMEOUT", "ACK_UNVERIFIED"}`
   (`telemetry.py:47`) excludes `ACKNOWLEDGED`, so the sweep keeps watching it and the only
   ways out are `VERIFIED` or `ACK_UNVERIFIED`. The `ACKNOWLEDGED` detail string is literally
   `"ACCEPTED — awaiting vehicle state verification"`.
2. `tx.transmitted` is set **only after** `transport.command_long()` returns
   (`telemetry.py:654-659`), with the comment naming the reason: the payload ledger uses that
   flag to decide whether a kit is still aboard or its fate is unknown. A command that was
   blocked, or whose write raised, never marks the kit as "sent and possibly dropped".

`SETTLING_TX_STATES = TERMINAL_TX_STATES | {"ACKNOWLEDGED"}` (`telemetry.py:54`) is a
separate, narrower idea: it is the set of moments the delivery engine must react to. It
exists because a servo release has no vehicle-state proof available (§4), so the engine has
to see the acceptance the instant it arrives. Conflating the two sets was the cause of the
CRITICAL defect in §7.

`_settle()` (`telemetry.py:499-512`) publishes the transaction and calls the settled handler
inside a try/except that escalates a handler failure to `severity="critical"` — a delivery
engine that throws is reported, not swallowed.

Recovery from link loss is deliberate. `_abandon_pending_transactions`
(`telemetry.py:196-212`) has two exits, and the reasoning in its docstring is sound:

- an ACK that was actually received but never verified becomes `ACK_UNVERIFIED`, described as
  *"the acceptance was received but the physical result was never seen"*;
- an unanswered command becomes `TIMEOUT`.

Marking a real `ACKNOWLEDGED` transaction as `TIMEOUT` because the link later dropped would
tell the operator the command never arrived, which is its own kind of false evidence.
Reconciliation on the delivery side is what turns `ACK_UNVERIFIED` into `UNKNOWN` kit status,
not into `DELIVERED`.

Timeouts are swept from the monitor loop (`telemetry.py:676-682`), not from ad-hoc `asyncio`
tasks, with the reason documented: commands are issued from the MAVLink reader thread, where
there is no running loop, and unreferenced tasks can be collected before they fire. That is
the correct choice, and it is the same class of bug that the simulation start path hit (§7,
C-2 family).

`_on_command_ack` rejects ACKs whose source component is not 1
(`telemetry.py:708-713`): a camera, gimbal or companion computer ACK cannot be evidence that
the vehicle took a flight command. Matching is last-writer-first within the same callsign and
command number (`telemetry.py:714`).

---

## 4. Per-command expectations, and the one command that has none

`_expectation_for()` (`telemetry.py:514-534`) is the contract for what "done" means:

| MAV_CMD | Expectation | Threshold |
|---|---|---|
| 176 `DO_SET_MODE` | `mode` | `custom_mode == params[1]` |
| 400 `COMPONENT_ARM_DISARM` | `armed` / `disarmed` | heartbeat armed bit; disarm also requires a known `custom_mode` |
| 22 `NAV_TAKEOFF` | `altitude_above_m` | ≥ 80 % of requested altitude, floor 1.0 m |
| 21 `NAV_LAND` | `altitude_below_m` | relative altitude ≤ 1 m |
| 20 `NAV_RETURN_TO_LAUNCH` | `mode_in` | RTL(6) / SMART_RTL(21) / CIRCLE(7) |
| 192 `DO_REPOSITION` | `mode_in` | GUIDED(4) / POSHOLD(16) / LOITER(5) / BRAKE(17) |
| 183 `DO_SET_SERVO` | **none** | — |

The 80 % takeoff factor and the mode sets are conservative in the right direction: a partial
climb or a loiter that never entered GUIDED cannot report `VERIFIED`.

**183 is the deliberate gap, and it is the most safety-relevant one.** `DO_SET_SERVO` carries
the payload release. ArduPilot answers `COMMAND_ACK` for it, but there is no telemetry message
the GCS can demand that proves the servo actually threw — `SERVO_OUTPUT_RAW` is not being read
in `_on_mavlink`, and even if it were it reports commanded output, not a released kit. So a
release command with no expectation resolves as `ACKNOWLEDGED` → "ACCEPTED (this command has
no vehicle-state check)" and never becomes `VERIFIED`.

The design answer in this codebase is physical evidence instead of a protocol answer: the kit
is only `CONFIRMED` when the beam/ground evidence arrives (`ledger.py`, `evidence.py`), and
the e2e run asserts that property — *every DELIVERED kit has beam evidence*, and
*UNKNOWN is never rewritten to DELIVERED*. The delivery engine reacts at the settling moment
(§3) precisely because protocol proof is unavailable.

The residual risk is that an operator reading the transactions list can see `ACKNOWLEDGED`
next to "release" and mentally downgrade the missing proof. Recommendation: add a
`SERVO_OUTPUT_RAW` **expectation** for 183 (weak proof, still better than none: it confirms
the output pin went to `release_pwm`), and label 183 transactions in the UI as
"protocol accepted — physical release unproven" until that exists. Both are logged in
`PRIORITISED_FIXES.md` as the deferred hardware-mapping item; `payload_hardware_verified` is
`false` in the profile, so implementing the check without the aircraft on the bench would be
guessing at the value.

---

## 5. Mission, fence and parameter upload

`upload.py` is the strongest part of the communication path and was already largely correct
before this review.

- `_upload_and_verify` (`upload.py:222-243`) reports `VERIFIED` only when **both** the
  `MISSION_ACK` path and a separate read-back pass agree; an acknowledged upload with a
  failing read-back returns the read-back result with a detail prefixed
  `"Upload acknowledged but …"`. Acknowledgement is not acceptance.
- Read-back (`upload.py:271-312`) requests each item individually via
  `MISSION_REQUEST_INT` and compares against the expected list in `_compare_items`
  (`upload.py:314`). The comment at `upload.py:296` names the exact failure mode it prevents:
  without the per-item comparison, a vehicle holding an older mission **of the same length**
  would report `VERIFIED`.
- Type-tolerant comparison is documented and narrow: frame aliases (e.g. the
  `TERRAIN_ALT` / `TERRAIN_ALT_INT` family) are folded with `_frame_class`
  (`upload.py:32-56`) so only a genuinely different frame class is a mismatch, and a
  coordinate difference beyond roughly 0.11 m (`upload.py:43`) is treated as a different
  waypoint — chosen because `MISSION_ITEM_INT` carries integer 1e-7 degrees.
- Fence and parameters are separate streams with their own receipts
  (`_fence_items` at `upload.py:501`, `_set_and_verify_params` at `upload.py:344`).
  Parameters are written and then read back through `PARAM_VALUE`; a write without a matching
  read is `UNVERIFIED`, not `VERIFIED`.
- `_overall_status` (`upload.py:187-204`) is fail-closed: any `FAIL` stream fails, any
  non-`VERIFIED` stream makes the whole receipt `UNVERIFIED`, and the mission stream is
  required when the plan has mission items.
- Timings are config-backed and bounded — `upload_timeout_s`, `readback_timeout_s`,
  `param_timeout_s` (profile 20/20/8 s), each `SAFETY_CRITICAL` in `config.py:77-79`.

The simulation path (`_simulated_receipts`, `upload.py:103-134`) returns `VERIFIED` receipts
tagged `"simulated": True` and the top-level receipt carries
`"rule": "A receipt is VERIFIED only when upload and read-back both match."` plus mode
`SIMULATION`, so a simulated receipt cannot be mistaken for a radio fact in the saved JSON.

One gap, unchanged and documented: mission-state streaming is gated on the transport being
`connected`, but the uploader runs on a worker thread and holds sessions on
`threading.Event`. That is correct design (it can be called from the reader thread), and the
timeout bounds it. What is **not** implemented is resumption: if a link drops mid-upload the
call fails and the operator must re-issue; there is no retry of the item sequence.

---

## 6. RFD868x telemetry and TX800 video are independent subsystems — verified

This was an explicit audit requirement. It holds, at the code level:

| | Telemetry (RFD868x) | Video (TX800) |
|---|---|---|
| Transport | `MavlinkTransport` — serial/UDP/TCP, `mavlink.py` | DirectShow capture device via `cv2.VideoCapture`, `video.py` |
| Addressing | SysID 1/2/3, GCS sysid 255, component 1 | Feed ID A/B/C ↔ receiver RX-A/B/C ↔ callsign, `config.video.feeds` |
| Threads | Reader thread per transport (`mavlink.py:96`) | Capture thread per feed + one health loop |
| Failure mode | `TransportError`, `heartbeat_age_s`, `stale` | `NO_SIGNAL` / `FROZEN` / `BLACK` / `CAPTURE_ERROR`, `frame_age_ms` |
| Mutual dependence | none | none |

There is no import of `video` from `mavlink.py`, no shared socket or device handle, and no
path where a video state change writes telemetry or a MAVLink message sets a feed status.
The only coupling is at the **readiness gate**, which is intentional and one-directional:
`preflight.py` reports `fleet.video.*` and `fleet.radio_topology` as separate lines and
`video.required_for_readiness` decides whether the video side holds the sortie. Killing the
video link cannot kill the command channel; killing the radio cannot corrupt the video
evidence.

One honest limitation: on a shared ground-station PC both subsystems compete for the same CPU,
USB bus and display, so a saturated machine degrades both. That is an operational constraint,
not a protocol coupling, and it is why the latency figures carry `latency_basis` labels
(`NOT_MEASURED` / `SYNTHETIC_FEED_NO_LINK` / `MEASURED`) rather than being presented as
proven.

---

## 7. Findings from this pass

Defect IDs in this document are namespaced. `C-1`/`C-2` are the same defects as
`C-1`/`C-2` in `INDEPENDENT_AUDIT.md`. The medium communication findings carry an `ML-`
prefix here: `INDEPENDENT_AUDIT.md` numbers its own `M-1…M-10` across the whole station,
and the two sequences were not the same list. The mapping is below so a fix recorded
against one document can be found in the other.

| ID here | Same finding in `INDEPENDENT_AUDIT.md` | Subject |
|---|---|---|
| ML-1 | M-4 | Freshness refreshed by any message, not only `HEARTBEAT` |
| ML-2 | M-1 | `MAV_CMD_DO_SET_SERVO` has no state expectation |
| ML-3 | M-8 | `COMMAND_ACK` matched without transaction correlation |
| ML-4 | M-3 | Transactions are not persisted across a restart |
| — | M-2, M-5, M-6, M-7, M-9, M-10 | Findings outside the communication path (retry policy, abort re-tasking, log buffer, replay vehicle shapes, MJPEG stale frame, the threading regression) |

| ID | File / function | Problem | Severity | Status |
|---|---|---|---|---|
| C-1 | `telemetry.py:47-54`, `_settle`, `_sweep_transactions` | `ACKNOWLEDGED` was in the terminal set used by the sweep, so an accepted command had no legal next state and never resolved. Every acknowledged command froze. | CRITICAL | **Fixed**; `TERMINAL_TX_STATES` / `SETTLING_TX_STATES` split, regression test in `backend/tests/` |
| C-2 | `telemetry.py:337-363` (`_on_mavlink`) | Independent topology short-circuited **all** traffic arriving on a named endpoint, so a correctly wired point-to-point link reported the aircraft offline and refused every command. | CRITICAL | **Fixed**; the interlock is now the sysid/endpoint *mismatch* check only (`:345-357`), with the reasoning left in the code as a comment so the filter does not return |
| ML-1 | `telemetry.py:370-377` | Any MAVLink message refreshes `last_seen_mono`/`connected`, not just `HEARTBEAT`. A non-heartbeating vehicle with position traffic looks fresh. | MEDIUM | **Open** — documented |
| ML-2 | `telemetry.py:514-534` | `MAV_CMD_DO_SET_SERVO` has no expectation and `SERVO_OUTPUT_RAW` is never parsed; release proof is beam evidence only. | MEDIUM | **Open** — hardware-blocked (`payload_hardware_verified: false`) |
| ML-3 | `telemetry.py:714-715` | ACK matching is by callsign + command number, not by `COMMAND_INT` parameters or a monotonic sequence. Two same-command transactions in flight resolve last-first. | MEDIUM | **Open** — the delivery engine issues one command per vehicle per step, so exposure is low; noted for the retry work |
| ML-4 | `telemetry.py:59-100`, `db.py` | Transactions live in a dict and are evicted past 500 (`telemetry.py:670-673`); they are not persisted. A GCS restart loses the command history that explains a kit's state. | MEDIUM | **Open** — event log persists the `command.*` publishes, so the audit trail survives even though the live table does not |
| L-1 | `telemetry.py:618` (`attempts = 1`) | `RETRYING` is a legal state in the sweep and in the ACK matcher but is never set anywhere; there is no retransmission path. | LOW | **Open** — tracked in `PRIORITISED_FIXES.md` item 26; deliberately left out for payload release, where a blind re-fire is worse than a timeout |
| L-2 | `mavlink.py:96-116` | The reader's reconnect loop sleeps 1 s + reopen + 2 s backoff and never gives up or reports a final failure count. | LOW | **Open** — operator-visible via `fleet.connection` state strings |
| L-3 | `config/fleet.json` | `state_verify_s` is 6.0 s in the profile while the in-code default is 8.0 s. A profile that fails to load would silently widen the window rather than break. | LOW | **Open** — harmless direction, noted so the two numbers stay in sync deliberately |

None of these change the conclusion in §2 or §6. The two CRITICAL items were real and are the
only pair in the whole audit that would have broken every command in REAL mode.

---

## 8. What this review does **not** establish

- That an RFD868x pair can actually link at the configured baud with the shipped antenna
  separation. No modem was present.
- That ArduPilot answers `DO_REPOSITION` in the mode set assumed at `telemetry.py:533`, or
  that `NAV_TAKEOFF` reaches 80 % of the requested altitude inside `command_timeout_s +
  state_verify_s`. These are timing assumptions against `PHYSICALLY TESTED`.
- That the `shared` topology genuinely carries three vehicles with distinct SysIDs — the
  profile says `topology_verified: false` and preflight fails on it. Correct, and it should
  stay failing until someone wires it.
- That a payload release ever physically occurred. `release_pwm: 1900` on channel 9 is a
  configured value, not a measured one.
- That mission read-back works against a real flight controller's mission storage, or that
  the frame-class aliasing at `upload.py:32-56` covers every ArduPilot variant in use.
- Any statement about range, latency, packet loss, or RF coexistence with the TX800 video
  link. Those need a bench and a field.

The e2e evidence in `docs/audit/TEST_RESULTS.md` is `SIMULATED` throughout: it proves the
state machine, the ledger invariants and the readiness gates behave as coded. It proves
nothing about radio.
