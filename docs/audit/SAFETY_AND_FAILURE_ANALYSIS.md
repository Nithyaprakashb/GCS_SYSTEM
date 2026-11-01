# Safety & Failure Analysis — RescueSwarm GCS

Independent safety pass over the release-decision path, the interruption paths, the
autopilot-protection boundary and the evidence-integrity rules.
Reviewer: Qoder (Principal Architect / Human Factors pass). Date: 2026-10-31.

Nothing in this document is `PHYSICALLY TESTED`. The conclusions are of two kinds and are
labelled separately: **structural** (provable by reading the code and by simulation — marked
`BUILT` / `SIMULATED`) and **residual** (dependent on hardware, RF behaviour or an aircraft
that has never flown — marked `PENDING TEST`).

---

## 1. The safety architecture as actually implemented

```
L5  Procedures      preflight gate, manual sign-off items, commissioning log
L4  Operator        ABORT (hold/loiter) and EMERGENCY RECALL (RTL) — global, keyword-typed
L3  GCS logic       RELEASE_CLEARANCE_V1, kit ledger, delivery state machine, safety engine
L2  Autopilot       ArduPilot fence / RTL / battery / RC failsafes on each vehicle
L1  Safety pilot    independent RC link (not part of this codebase, not verifiable here)
```

The layering claim that matters is the direction of dependency, and it holds in code: L2 is
*configured* by the GCS during setup (`upload.py:_parameter_plan`) but never *armed* by it at
run time. `clear_recall` states the boundary explicitly (`mission_engine.py:648-674`):
*"Ground recall flag cleared. Aircraft already in RTL remain in RTL; onboard failsafes are not
under GCS control."* and returns `"Aircraft autonomy is never overridden from the ground."`

A failed GCS process therefore cannot disable onboard protection. It can stop *asking*, it
cannot start *un-asking*. The one caveat is §6.

**AI has no place in the safety path.** `copilot.py` opens with
*"Advisory only. The copilot cannot arm, fly, change a mission or release a kit"* and is
implemented as a deterministic reader of recorded state with an *optional* local Ollama
enhancement (`copilot.py:29-53`, `_rule_based` is the primary answer, `_ollama` at `:286` is a
garnish that must fail soft). The optional LLM is not essential: with Ollama absent the copilot
still answers from `_rule_based`, and no gate, interlock or command reads from it. The
deterministic constraints in `safety.py` and `delivery.py` never consult the copilot at all.

---

## 2. The release gate

`RELEASE_ELIGIBLE = {"HOVER_VERIFY"}` (`delivery.py:47`) is the structural half of the rule: a
servo command can only be issued when the delivery state machine is in `HOVER_VERIFY`, and the
state machine has no edge into that state from anywhere but `DESCENT`. The behavioural half is
`SafetyEngine.release_clearance` (`safety.py:32-151`), which evaluates eleven named checks and
computes `allowed = all(check["ok"])` — an AND over evidence, not a score.

| Check | Rule | Why it is the right rule |
|---|---|---|
| `recall_active` | must be false, **first in the list** | *"A commanded recall outranks every pending delivery"* — including a task already at hover with a kit reserved |
| `kit_reservation` | ledger must say RESERVED for *this* survivor | one physical kit cannot be promised to two people |
| `telemetry_fresh` | connected ∧ ¬stale ∧ `identity_valid` | identity, not just traffic |
| `armed` | heartbeat armed bit | |
| `mode_guided` | `mode == "GUIDED"` | release only in a controlled mode |
| `drop_height` | `abs(AGL − drop_height) ≤ 1.0 m` | TFmini-S rangefinder height, tight tolerance |
| `hover_stable` | `speed ≤ 0.6 m/s` **and** `hover_verified` | the timer must have completed, not just the number |
| `target_inside_fence` | `> 0.5 m` inside the inclusion polygon | drop point inside the boundary the aircraft itself will not leave |
| `traffic_known` | every other vehicle must have a fresh 3-D position | *"position unavailable; descent denied"* — absent data blocks, it does not permit |
| `traffic_fresh` | stale sibling ⇒ descent denied | same principle |
| `clearance_<callsign>` | horizontal distance ≥ `descent_clearance_m` | the 20 m separation rule |

Failure publishes `safety.interlock / RELEASE_DENIED` with the full check list and every reason,
severity `warning`, and carries `"rule": "RELEASE_CLEARANCE_V1"` plus
`measurement_status` = `SIMULATED` or `LIVE`. The denial is evidence, not a toast.

Two properties worth naming because they are the difference between a demo and a system:
**missing data is treated as unsafe**, and **the gate is evaluated at the moment of the command,
not at plan time**.

---

## 3. Command lifecycle and the ACK trap

The audit's specific requirement — *"do not interpret ACK_ACCEPTED as proof that the requested
physical action has finished"* — is enforced structurally (see
`MAVLINK_COMMUNICATION_REVIEW.md` §3 for the line-by-line version):

- `ACKNOWLEDGED` is excluded from `TERMINAL_TX_STATES` (`telemetry.py:47`) so the sweep keeps
  watching it, and the only exits are `VERIFIED` or `ACK_UNVERIFIED`.
- `tx.transmitted` is set only after the transport write returns (`telemetry.py:654-659`) — the
  ledger's distinction between "kit still aboard" and "kit fate unknown" keys off that flag.
- `_expectation_for` defines vehicle-state proof for 176/400/22/21/20/192. **183 `DO_SET_SERVO`
  has none**, so release is proven by beam evidence only. An operator can see `ACKNOWLEDGED`
  next to a release; the honest reading is "protocol accepted, physical release unproven".

### Delivery state machine

`ALLOWED` (`delivery.py:26-44`) is a closed graph. Reviewing every edge for reachability and
for interruption correctness:

```
QUEUED → LAUNCHING → HOLD_CLEARANCE → TRANSIT → DESCENT → HOVER_VERIFY → ACK_PENDING
                                                                              ↓
CONFIRMED ← RELEASE_SENT → UNKNOWN                                     CLIMB → RETURNING
   ↓            ↓                                                     RETURNING   ↓
CLIMB ──────────┘                                          STANDBY ← LANDING ← ───┘
```

Properties verified against the table:

- **No accidental release.** The only edge into `ACK_PENDING` is from `HOVER_VERIFY`; the only
  edge into `RELEASE_SENT` is from `ACK_PENDING`; nothing reaches `RELEASE_SENT` from transit or
  descent.
- **Abort is reachable from every non-terminal state.** `ABORTED` is an allowed successor of
  QUEUED, LAUNCHING, HOLD_CLEARANCE, TRANSIT, DESCENT, HOVER_VERIFY, ACK_PENDING and of
  `RETURNING`'s chain via `ABORTED → STANDBY`.
- **`ACK_PENDING → UNKNOWN` exists and is the honest terminal for a stranded release.** The
  comment names the case: *"An aborted sortie can strand a release command mid-flight; that kit
  has left the magazine with no evidence, so UNKNOWN is its honest terminal state."*
- **`UNKNOWN → CLIMB`** lets the aircraft come home without the kit's status being rewritten to
  `CONFIRMED`. `ASSIGNMENT_STATUS["UNKNOWN"] = "UNKNOWN"` and
  `SURVIVOR_STATUS["UNKNOWN"] = "UNKNOWN"` — the unknown never gets dressed up as served.
- **`STANDBY` is the only absorbing state** (`:42`), so a parked vehicle cannot be resurrected
  into a delivery by a late event.
- Internal machine states do not leak to the operator: `CLIMB`/`STANDBY` map to `DELIVERED`
  in the assignment badge (`delivery.py:49-52`).

---

## 4. Interruption paths, audited one by one

### 4.1 EMERGENCY RECALL (`mission_engine.py:541-556`)

Sets the GCS latch (`safety.set_recall`) **before** transmitting, so the release gate closes
immediately even if the radio is dead. Records `EMERGENCY_RECALL` as an operator action. In
REAL it commands `NAV_RETURN_TO_LAUNCH` (20) to every callsign; the transaction then goes
through the normal lifecycle, whose expectation is `mode_in [6, 21, 7]` — RTL / SMART_RTL /
CIRCLE — so an RTL that never actually engaged surfaces as `ACK_UNVERIFIED` rather than silence.

**Deviation from the requirement (see §7, S-2):** the reference matrix (`docs/reference/09`,
row 6) says the command *"is sent repeatedly until each heartbeat shows RTL"*. The code fires
one command per vehicle per press. `RETRYING` is never set anywhere (`telemetry.py:618` leaves
`attempts = 1`). A single lost RTL command is not re-issued by the station.

### 4.2 ABORT (`:558-577`)

Commands `DO_SET_MODE` to LOITER (params `[1, 5, …]`) per vehicle, then reconciles the kit
ledger, records `MISSION_ABORT`, finalises the phase to `ABORTED` and stops recording. LOITER
rather than LAND is defensible for a multi-vehicle pad — but note that LOITER does not consume
battery toward a landing, so abort is "hold", not "recover". The result payload says so in
plain text: *"Abort holds the fleet in LOITER. Kits without beam evidence are UNKNOWN and are
never re-released automatically."*

### 4.3 Kit reconciliation on abort (`_reconcile_kits_on_abort`, `:579-646`)

This is the strongest safety reasoning in the codebase, and it is worth restating because it is
exactly the kind of rule that gets faked:

| Delivery state at abort | Ledger condition | Action | Result |
|---|---|---|---|
| QUEUED…HOVER_VERIFY | release never commanded | `ABORTED → RETURNING` | kit still aboard |
| ACK_PENDING / RELEASE_SENT | `RESERVED` **and** `tx.transmitted` | `note_command_abandoned`, `UNKNOWN`, `beam_result="NO_EVIDENCE"` | **never re-fired, never recycled** |
| ACK_PENDING / RELEASE_SENT | `RESERVED`, not transmitted | `cancel_reservation` | returned to magazine |
| RELEASE_SENT | `load_status == RELEASE_SENT` | `note_command_abandoned`, `UNKNOWN` | fate unknown |
| any | `RESERVED` with no active task | `cancel_reservation` (sweep, `:641-645`) | returned |

The comment at `:609-611` is the safety property in one sentence: *"Whether the latch fired is
unknown, and 'unknown' must never be recycled into 'still aboard' — that is how one physical kit
gets promised to two people."*

Verified in the end-to-end abort run: 10 kits accounted for, `{"DELIVERED": 1, "LOADED": 9}`,
`returned=0 unverified=0`, terminal phase `ABORTED`, no kit in a half-state, no delivery stuck in
`ACK_PENDING`. (`SIMULATED`.)

### 4.4 Recall clear (`:648-674`)

Clears only the ground latch, publishes `RECALL_CLEAR_REAL` as a warning, leaves RTL aircraft in
RTL. Correct direction of authority.

### 4.5 Link loss, transport death, video loss

- Telemetry: `_monitor_loop` ages `stale` against `stale_after_s` (2.0 s) and link loss against
  `heartbeat_timeout_s` (3.0 s); `_address_block` then refuses every command with
  `NOT_ADDRESSED`, publishing a `critical` `COMMAND_NOT_ADDRESSED` interlock. No command can be
  silently queued to an aircraft the station cannot see.
- Pending commands on a dying link: `_abandon_pending_transactions` (`:196-212`) splits
  `ACK_UNVERIFIED` (acceptance really arrived) from `TIMEOUT` (it did not), because *"telling the
  operator the command never arrived is its own kind of false evidence"*.
- Mode switch: `connect()` calls `_reset_vehicle_records()` so a simulated heartbeat cannot
  leave `connected=True` behind into REAL and pass the freshness gate with zero radio traffic.
  This was a real hole; it is closed and tested.
- Video: see `VIDEO_SYSTEM_REVIEW.md` — video cannot affect the command channel, and a
  started-but-silent feed can no longer be reported `LIVE`.

### 4.6 Reachability of the emergency controls

`App.tsx` renders ABORT and EMERGENCY RECALL in the shell header, so they are present on every
screen including Setup, Mission, Replay, Logs and Settings — the requirement *"reachable from
every mission screen"* is met by construction, not per-screen. They require a typed keyword
(`ABORT` / `RECALL`) in a modal before dispatch (`App.tsx:115`), and both carry `aria-label`.

Two deliberate trade-offs, recorded rather than hidden:

- The buttons are **enabled** on `emergencyLive = executing ∨ airborne ∨ recall`
  (`App.tsx:77-83`) — deliberately *wider* than `phase === "EXECUTING"`, because *"a phase that
  has not caught up, an aborted sortie with aircraft still airborne, or a link that has dropped
  must never cost the operator the recall button."* When no aircraft is airborne and no sortie is
  running the control shows a stated reason
  ("No sortie executing and no aircraft reporting airborne") instead of firing into nothing.
- If the local ground service is unreachable the buttons state
  *"Local ground service is unreachable — nothing can be transmitted"*. That is honest: the
  station is not pretending to command. The consequence is that a GCS UI freeze costs the
  operator the *software* recall path — which is exactly why L1/L2 exist and are not GCS-owned.

---

## 5. Evidence integrity

The rule under test is that the system must never present a simulated, estimated or absent
measurement as a confirmed one. What enforces it:

| Mechanism | Location | What it prevents |
|---|---|---|
| `data_quality` on every bus event (`LIVE` / `SIMULATED` / `UNAVAILABLE`) | `events.py`, every publisher | a simulated value being read as radio truth |
| `provenance` chip on the map | `components/TacticalMap.tsx` | replay or simulation looking like live telemetry |
| `beam_result` required for `CONFIRMED` | `delivery.py:363-379` | a kit marked delivered with no physical evidence |
| `UNKNOWN` never rewritten to `DELIVERED` | e2e assertion | abort or timeout laundering a kit into success |
| Receipts `VERIFIED` only on upload ∧ read-back match | `upload.py:222-243` | an acknowledged-but-wrong mission passing as correct |
| `latency_basis` labels | `video.py:70-73` | an invented glass-to-glass number |
| Protected config keys (`topology_verified`, `radio_region`) | `config.py:61`, `:83` | an API call "verifying" a physical commissioning step |
| Recorder session close-out with `complete` + `frames_recorded` | `video.py:820-827`, `db.py` | an empty or truncated file reading as complete evidence |
| Preflight FAIL vs manual sign-off | `preflight.py`, `system.py` | a simulated PASS in REAL mode |

The e2e harness asserts the invariants directly (10 kits accounted for, no half-states, every
DELIVERED kit with beam evidence, no `ACK_PENDING` residue, event idempotency index held). That
is `SIMULATED` evidence that the bookkeeping is right; it is not evidence about a latch.

---

## 6. Onboard protection is *partly* unprovisioned — the most important finding in this pass

The design document (`docs/reference/09_FAIL_SAFE_FEATURES_AND_SAFETY.md` §2) lists the
autopilot fail-safes and adds: *"The GCS writes these parameters to each drone during setup and
reads them back. Any mismatch fails the pre-flight check."*

The GCS does write a parameter plan and does read it back. The plan is smaller than the
document claims:

**Written by `_parameter_plan` (`upload.py:525-547`)**
`FENCE_ENABLE=1`, `FENCE_TYPE=5`, `FENCE_ACTION=1`, `FENCE_ALT_MAX`, `RTL_ALT` (25/18/22 m,
matching the spec per airframe), `FS_THR_ENABLE=1`, `BATT_FS_LOW_ACT=2`, `BATT_FS_CRT_ACT=1`,
`WPNAV_SPEED`.

**Required by the spec but never written**

| Parameter | Spec row | Consequence |
|---|---|---|
| `FS_GCS_ENABLE` (+ `SYSID_MYGCS`) | row 2, comm-loss → RTL | **the GCS never transmits its own MAVLink HEARTBEAT either** (no `heartbeat_send` in `telemetry.py`), so the autopilot has no GCS link to time out. The documented comm-loss failsafe does not exist on the vehicle. |
| `FS_EKF_ACTION` | row "+", EKF/GNSS failure → LAND | EKF failsafe left at whatever the vehicle holds |
| `BATT_LOW_VOLT` / `BATT_CRT_VOLT` | rows 3 / 3b | the actions are armed (`BATT_FS_LOW_ACT=2`, `…CRT_ACT=1`) but the **thresholds are not provisioned**. `battery_low_pct` / `battery_critical_pct` exist in `config/fleet.json` and are read by nothing in `backend/` or `desktop/src/` (grep across the repo: config only). |
| `FENCE_RADIUS` + `FENCE_TYPE` bit 2 | row 1 | **deliberately** deferred, correctly: *"enabling bit 1 would arm a breach monitor whose trigger distance is whatever FENCE_RADIUS happens to hold"* (`upload.py:528-532`). This one is right; the others are gaps. |

This is the classic failure mode the audit was looking for: a documented safety layer that the
implementation only partly provides, invisible in simulation because the simulator never reads
autopilot parameters. The GCS's own energy model (`safety.return_energy_ok`, using
`mission.battery_reserve_pct` and `battery_delivery_pct_per_min`) is *separate* from the
autopilot's battery failsafe and does not compensate for it — the GCS cannot land an aircraft it
cannot reach.

Severity `HIGH` (hardware-mapping + incomplete safety, priority bands 1 and 4). Not fixed here
because choosing voltages for a 6S 22 Ah pack without a measured discharge curve would be
inventing hardware behaviour, which the standing constraints forbid. Tracked as S-1 in §7 and in
`PRIORITISED_FIXES.md`.

---

## 7. Failure-mode register

Structural findings from this pass. `Status` says what a reviewer can verify today.

| ID | Failure mode / gap | Detection today | Response today | Severity | Status |
|---|---|---|---|---|---|
| S-1 | Onboard comm-loss / EKF / battery-threshold failsafes not provisioned; GCS sends no heartbeat | none | none | **HIGH** | **Open** — hardware-blocked, `PENDING TEST` |
| S-2 | RECALL is single-shot; spec requires repetition until each heartbeat shows RTL | `ACK_UNVERIFIED` after `state_verify_s` | operator must press again | **HIGH** | **Open** — `RETRYING` never set |
| S-3 | Any MAVLink message refreshes freshness, not only HEARTBEAT (`telemetry.py:370-377`) | `heartbeat_age_s` is displayed but does not gate | commands proceed | MEDIUM | **Open** |
| S-4 | `DO_SET_SERVO` has no vehicle-state proof; release known only from beam evidence | `CONFIRMED` requires beam evidence | `UNKNOWN` without it | MEDIUM | **Open**, honest by design |
| S-5 | Transactions not persisted; evicted past 500 (`telemetry.py:670-673`) | event log survives | restart loses the live command table | MEDIUM | **Open** |
| S-6 | Abort → LOITER, not LAND: a held-abort fleet still burns battery with no autonomous descent | operator | operator must recall or land individually | MEDIUM | **Open**, documented in the abort response |
| S-7 | Survivor whose delivery aborted mid-transit is not re-allocated automatically | `SURVIVOR_STATUS = UNSERVED` | operator re-runs allocation | LOW | **Open** |
| S-8 | `REJECTED` on `NOT_ADDRESSED` is terminal; no queue or deferred retry when a link recovers | `critical` interlock | operator action | LOW | **Open** |
| S-9 | Preflight manual items are operator-signed; a careless sign-off opens the readiness gate | signature recorded in DB | none | MEDIUM | **Open** — inherent to any checklist; mitigate by requiring the item text to be echoed |
| S-10 | No automated regression asserts that a REAL-mode gate cannot PASS on simulated data | covered only by code paths that branch on `mode` | — | MEDIUM | **Open** — recommended test, see `TEST_RESULTS.md` |

**Closed by this audit** (full detail in `INDEPENDENT_AUDIT.md` and
`PRIORITISED_FIXES.md`): C-1 `ACKNOWLEDGED` frozen forever · C-2 independent topology dropped
all traffic · lifecycle lock on video start/stop · started-but-silent feed reported `LIVE` ·
empty recording accepted as evidence · orphaned `video_sessions` row · mode-switch telemetry
residue · stale-sysid transaction misclassification · abort kit reconciliation.

---

## 8. What this analysis does not establish

- No statement here has been tested on an aircraft. The release gate, the RTL behaviour, the
  fence and the battery model are all `BUILT` + `SIMULATED`.
- That a real servo latch fires at `release_pwm: 1900` on channel 9.
  `payload_hardware_verified: false` — deliberately.
- That ArduPilot's own pre-arm, fence or failsafe behaviour matches the assumptions encoded in
  `_parameter_plan`. Reading them back (`upload.py:344-395`) proves the write, not the effect.
- That beam evidence reliably distinguishes released from stuck payload at real altitudes,
  lighting and RSSI. `PENDING TEST`.
- That the ground station survives three simultaneous ffmpeg captures plus MAVLink plus SQLite
  on the target laptop under thermal load.
- Anything about RF: comm-loss timing at range, multipoint RFD868x capacity under three vehicles,
  or 5.8 GHz video coexistence. There is no hardware, so there is no measurement.

**This GCS is not flight-ready.** The safety *logic* is coherent and better than most demos in
this space; the safety *provisions* on the vehicle are incomplete (S-1), the recall path is not
yet redundant (S-2), and nothing has crossed from simulation to a bench.
