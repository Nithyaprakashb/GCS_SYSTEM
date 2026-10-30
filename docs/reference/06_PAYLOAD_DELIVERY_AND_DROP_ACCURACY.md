# Payload Delivery and Drop Accuracy — 25 marks

Team ADRODS-X · NIDAR PS1 · Phase 4A design review · Rubric row 6

## Key message

Each delivery hexacopter carries 5 kits in a gravity magazine with a load-bearing 2-stage escapement that drops exactly one box only when a strict hover gate is met at 3 m, and a break-beam-backed kit ledger counts every kit once; because the drop point comes from the INS-01 geotag, we keep our own hover and fall errors small and will measure the zone mix rather than claim Zone A.

---

## Slide plan

### Slide 1: Payload and one-box-at-a-time release

- 10 kits of 200 g (20 x 10 x 5 cm): 5 on DEL-01 and 5 on DEL-02, pooled across the fleet.
- Gravity magazine plus a 2-stage escapement: the next box is locked before the exit opens, so only one box can leave.
- Two MG90S servos only trip the latches; the latch structure carries the load (servo stall 2.2 kgf.cm is below the 4 kgf.cm a load-bearing servo would need).
- IR break-beam at the exit, read by a Lua script on the Cube Orange+, confirms each box left.
- Bench target before flight: at least 50 consecutive single-box releases.

Suggested visual: side-view sketch of the magazine with the two latches and the beam, next to the 5-step escapement table.

### Slide 2: Delivery sequence, release gate and kit ledger

- GCS sends a GUIDED go-to at the transit altitude (DEL-01 18 m, DEL-02 22 m).
- Descent only after clearance (INS-01 and the other DEL at least 20 m away); descend to 3 m over the geotag using the TFmini-S.
- Release gate: within 0.5 m of the target, ground speed below 0.35 m/s, height 3 m +/- 0.4 m, all held for 1 s.
- Kit ledger: AVAILABLE -> RESERVED -> RELEASE_COMMANDED -> BEAM_CONFIRMED -> COUNTED.
- No beam -> UNKNOWN, never re-fired automatically (avoids two kits on one survivor).
- Then next queued survivor or RTL.

Suggested visual: sequence diagram (GCS / DEL / gate) and the ledger state machine from Sections 4 and 6.

### Slide 3: Drop accuracy: what drives it and how we will measure it

- Free fall from 3 m takes 0.782 s; drift is 0.39 / 0.78 / 1.56 m at 0.5 / 1 / 2 m/s residual speed. Our 0.35 m/s gate limits it to about 0.27 m.
- Drop error = geotag error (dominant) + hover/release error (about 0.4-0.8 m expected) + wind drift.
- DEL drones have no on-board vision, so drop accuracy depends on the INS-01 geotag.
- Mitigations: multi-frame geotag averaging, hover verification, EKF wind estimate to offset the release point, calibrated release delay.
- Zone A (1 m) = 20, B (2 m) = 14, C (3 m) = 8 marks per kit. We do not claim Zone A; the zone mix will be measured on surveyed markers.

Suggested visual: target rings at 1, 2 and 3 m with arrows for each error term, and the free-fall drift table.

---

## 1. Delivery task and scoring

| Item | Value |
|---|---|
| Kits | Up to 10 survivors, one 200 g kit each, 20 x 10 x 5 cm |
| Fleet load | 5 kits on DEL-01, 5 on DEL-02 (1.0 kg each) |
| Zone A | Kit within 1 m of the survivor: 20 marks |
| Zone B | Within 2 m: 14 marks |
| Zone C | Within 3 m: 8 marks |
| Time cap | 30 min for the whole mission |
| Manual input | -50 per input; delivery is fully automatic after START |

Marks arithmetic (not a prediction):

| Outcome for 10 kits | Marks |
|---|---:|
| All in Zone A | 200 |
| All in Zone B | 140 |
| All in Zone C | 80 |
| Example mix: 2 A + 5 B + 3 C | 40 + 70 + 24 = 134 |

Each zone step is worth 6 marks per kit (C to B, B to A). Moving all 10 kits up one zone is worth 60 marks, which is why every error term below matters.

---

## 2. Delivery drone configuration

| Item | DEL-01 / DEL-02 |
|---|---|
| Frame | 900 mm X6 hexacopter |
| All-up mass | 5.414 kg loaded, 4.414 kg after 5 drops (BOM, not weighed) |
| Kits as share of mass | 1.0 kg = 18.5% |
| Hover thrust per motor | 902 g loaded (41% of an unverified 2.2 kg reference max), 736 g after 5 drops |
| Mission endurance on paper | 37.6 min loaded (unverified thrust curve) |
| Flight controller | Cube Orange+, M10 GNSS |
| Height sensor | TFmini-S downward lidar (3 m is well inside its range) |
| Release | 5-box gravity magazine + 2-stage load-bearing escapement, 2 x MG90S on a dedicated servo BEC |
| Exit sensor | IR break-beam read by a Lua script on the Cube Orange+ |
| Video | RunCam Nano 2 + TX800 analog to the GCS (operator view only) |
| Companion computer | None; the GCS commands every delivery step |
| Transit altitude | DEL-01 18 m, DEL-02 22 m |

---

## 3. Release mechanism

### 3.1 Layout

```text
          +-----------+
          |   box 5   |
          |   box 4   |      gravity magazine (boxes stacked, for example flat: 5 x 5 cm = 25 cm)
          |   box 3   |
          |   box 2   |
    ==>   |-----------|   <==  upper stage: pawls engage box 2 from the sides
          |   box 1   |                     (servo A trips them; the frame carries the load)
    ==>   |===========|   <==  lower stage: latch under box 1
          |   exit    |                     (servo B trips it; the frame carries the load)
    ->  - - - - - - - - -  <-  IR break-beam across the exit (Lua script on the Cube)
                |
                v   box falls 3 m in 0.78 s
```

### 3.2 Escapement cycle (exactly one box per cycle)

| Step | Upper stage | Lower stage | What happens |
|---:|---|---|---|
| 0 Ready | Open | Closed, carries the stack | 5 boxes rest on the lower latch |
| 1 | Closes on box 2 | Closed | Boxes 2-5 now held by the upper stage; box 1 is isolated |
| 2 | Closed | Opens | Box 1 alone falls through the beam |
| 3 | Closed | Closes | Exit shut again |
| 4 | Opens | Closed | Boxes 2-5 drop one position onto the lower latch; ready for the next survivor |

The exit (lower stage) can open only while the upper stage holds the rest of the stack, so a single command cannot release two boxes.

### 3.3 Why the servos must not carry the load

| Quantity | Value |
|---|---|
| Stack weight | 5 x 200 g = 1.0 kg |
| Lever arm if a servo horn held the stack | 2 cm |
| Static torque | 1.0 kg x 2 cm = 2.0 kgf.cm |
| Required with x2 safety factor | 4.0 kgf.cm |
| MG90S stall torque | 2.2 kgf.cm |
| Result | 2.2 < 4.0: a servo alone fails the check |

Design consequence:

- The latches are load-bearing; the load goes into the magazine frame, not the servo gears.
- The servos only move a latch out of the way (trip), which needs far less torque than holding 1 kg.
- Latches hold when the servos are unpowered, so a servo power loss cannot open the exit.
- The servos run from a dedicated BEC so a servo stall cannot brown out the Cube Orange+.

### 3.4 Exit confirmation

- An IR break-beam pair spans the exit.
- A Lua script on the Cube Orange+ reads it on a spare AUX pin configured as GPIO and reports to the GCS over MAVLink.
- After each release command the script watches a short window (length set from bench timing).
- Beam broken in the window -> BEAM_CONFIRMED. No break -> UNKNOWN.

Bench checks before any flight (PENDING):

| Test | Method | Target |
|---|---|---|
| B1 Single-box release | At least 50 consecutive cycles, all 5 positions, with vibration and at low servo voltage | 50 of 50 single boxes, zero double releases, zero jams |
| B2 Beam reliability | Count beam events against boxes released in B1; check vibration and sunlight | One event per box, no false triggers |
| B3 Load path | 2 kg static load (x2 of the full stack) on the latches with servos unpowered | No slip |
| B4 Kit survival | Drop a packed kit from 3 m (impact about 7.7 m/s) onto grass, soil and hard ground, 10 times | Kit intact; bounce and slide distance recorded |
| B5 Release delay | Time from release command to beam break | Measured value used in the release offset |

---

## 4. Delivery sequence

```text
 GCS                                   DEL-0x (Cube Orange+)                 Gate / beam
  | survivor assigned, kit RESERVED       |                                      |
  |-- GUIDED go-to, transit altitude ---->|  18 m (DEL-01) / 22 m (DEL-02)       |
  |<-- arrived over geotag ---------------|                                      |
  | INS-01 and other DEL >= 20 m away?    |                                      |
  |-- descent clearance ----------------->|  descend to 3 m (TFmini-S)           |
  |<-- position, speed, range ------------|                                      |
  | gate: <= 0.5 m, < 0.35 m/s,           |                                      |
  |       3 m +/- 0.4 m, held for 1 s     |                                      |
  |-- release (escapement steps) -------------------------------------------->  | one box out
  |<-- Lua: beam broken / no beam ---------------------------------------------- |
  | ledger: BEAM_CONFIRMED -> COUNTED     |                                      |
  |        (or UNKNOWN)                   |                                      |
  |-- next target or RTL ---------------->|  climb to transit altitude           |
```

| Step | Action | Who decides | Sensor |
|---:|---|---|---|
| 1 | Go to the survivor geotag at the transit altitude (10 m/s planning speed) | GCS | M10 GNSS / EKF |
| 2 | Wait for descent clearance: INS-01 and the other DEL at least 20 m from the drop point | GCS | Telemetry of all three drones |
| 3 | Descend to 3 m over the geotag | GCS command, Cube executes | TFmini-S |
| 4 | Hold until the release gate is met for 1 s | GCS | EKF position and velocity, TFmini-S |
| 5 | Release: the 2-stage escapement drops exactly one box | GCS command | - |
| 6 | Confirm exit; update the ledger | Lua script on the Cube reports; GCS records | IR break-beam |
| 7 | Climb to the transit altitude; next queued survivor or RTL | GCS | - |

Service time per kit (ASSUMED planning value 25 s):

| Part | DEL-01 (from 18 m) | DEL-02 (from 22 m) |
|---|---:|---:|
| Descend to 3 m at 1.5 m/s (ArduPilot default, to be tuned) | 10.0 s | 12.7 s |
| Settle and meet the 1 s gate (ASSUMED typical) | 4 s | 4 s |
| Release and beam window | 1 s | 1 s |
| Climb back at 2.5 m/s (ArduPilot default) | 6.0 s | 7.6 s |
| **Total** | **about 21 s** | **about 25 s** |

---

## 5. Release gate

| Condition | Threshold | Measured by | Why this value |
|---|---|---|---|
| Horizontal position | Within 0.5 m of the geotag | DEL EKF / GNSS position | Keeps our own share of the error small |
| Ground speed | Below 0.35 m/s | EKF velocity | Drift during the 0.78 s fall stays below about 0.27 m |
| Height | 3 m +/- 0.4 m | TFmini-S | Fall time 0.73-0.83 s, so drift is predictable |
| Duration | All three true together for 1 s | GCS timer | Rejects a momentary pass while swinging through |
| Airspace | INS-01 and the other DEL at least 20 m away | GCS | Safety during descent |

Important limits of the gate:

- The 0.5 m check compares the DEL's own GNSS position with the geotag. It does not check the true survivor position; the geotag error is outside the gate.
- Gate values are design values; they will be tuned from hover logs.

Timeout (proposed, to be finalised from tests): if the gate is not met within a set time, the drone climbs and re-approaches once. If it still fails, the GCS logs it; whether to release with a wider gate (a kit in Zone B or C scores more than no kit) will be decided from hover-test data.

---

## 6. Kit ledger

```text
 AVAILABLE --(allocator assigns a survivor)--> RESERVED --(gate met, release sent)--> RELEASE_COMMANDED
     ^                                            |                                       |
     |  (drone fails: reservation cancelled,      |              beam broken in window    |   no beam in window
     |   survivor reassigned to the other DEL)    |                       v               v
     +--------------------------------------------+               BEAM_CONFIRMED        UNKNOWN
                                                                          |          (never re-fired
                                                                          v           automatically)
                                                                       COUNTED
```

| State | Meaning | Counts as delivered | Free for a new survivor |
|---|---|---|---|
| AVAILABLE | In the magazine, not assigned | No | Yes |
| RESERVED | Assigned to exactly one survivor ID | No | No |
| RELEASE_COMMANDED | Release sent; beam window open | No | No |
| BEAM_CONFIRMED | Beam broken in the window (Lua report received) | Being recorded | No |
| COUNTED | GCS has logged the kit against the survivor ID; survivor shown DELIVERED | Yes | No |
| UNKNOWN | Release sent, no beam seen; flagged for review | No | No |

Ledger rules:

- One survivor ID can hold at most one kit. Duplicate sightings (merged within 5 m) never reserve a second kit.
- Free kits for allocation = AVAILABLE kits on drones that are candidates.
- An UNKNOWN kit is never re-fired automatically: the box may have left without breaking the beam, and a second release could put two kits on one survivor while another survivor gets none.
- Every state change is time-stamped in the GCS log.

---

## 7. Drop physics

### 7.1 Free fall from 3 m

t_fall = sqrt(2h / g) = sqrt(2 x 3 / 9.81) = 0.782 s. Impact speed = g x t = 7.67 m/s (no drag).

| Residual horizontal speed at release | Drift during the fall (v x 0.782 s) |
|---:|---:|
| 0.35 m/s (gate limit) | 0.27 m |
| 0.5 m/s | 0.39 m |
| 1.0 m/s | 0.78 m |
| 2.0 m/s | 1.56 m |

| Release height (gate band) | Fall time | Drift at 0.35 m/s |
|---:|---:|---:|
| 2.6 m | 0.728 s | 0.25 m |
| 3.0 m | 0.782 s | 0.27 m |
| 3.4 m | 0.833 s | 0.29 m |

### 7.2 Other small terms (CALCULATED examples)

| Term | Example | Effect |
|---|---|---|
| Wind on the falling box (drag coefficient 1.05 and 0.01 m2 side area ASSUMED) | 3 m/s wind | about 0.09 m |
| Same | 5 m/s wind | about 0.25 m (upper bound) |
| Release delay (command to box clear) | 0.3 s at below 0.35 m/s | about 0.1 m or less |
| Mass change at release | 200 g of 5.414 kg (3.7%) to 200 g of 4.614 kg (4.3%) | Brief upward push of 0.38-0.44 m/s2; a few centimetres if the controller responds in 0.5 s |

### 7.3 Error chain from survivor to kit

```text
 true survivor --[geotag error, INS-01]--> geotag --[DEL GNSS + hover/release error]--> release point
       --[fall drift: residual speed + wind]--> impact point --[bounce / slide]--> final kit position
```

| Term | Expected size | Type | Main mitigation |
|---|---|---|---|
| Geotag error (INS-01) | Target median 2 m or less, 95th percentile 3 m or less | Target, PENDING TEST | Multi-frame averaging, calibration (row 5) |
| DEL GNSS vs INS-01 GNSS | Partly common to both receivers; size unknown | PENDING TEST | Same receiver type, same sky, minutes apart |
| Hover / release error | About 0.4-0.8 m expected | ASSUMED | Release gate (0.5 m, 0.35 m/s, 3 m, 1 s) |
| Residual-speed drift | 0.27 m or less | CALCULATED | Speed gate |
| Release delay | About 0.1 m or less | CALCULATED example | Calibrated release delay (B5) |
| Wind on the box | About 0.09-0.25 m at 3-5 m/s | CALCULATED on ASSUMED drag | EKF wind estimate used to shift the release point upwind |
| Bounce / slide after impact | Unknown | PENDING TEST | Packaging; measured in B4 |

What this means:

- The geotag is the dominant term. DEL drones have no on-board vision, so they cannot correct a geotag error at the drop point.
- Our gate keeps the hover, fall and wind terms to well under 1 m in these examples.
- With a GNSS-class geotag, Zones B and C are the realistic planning case. We do not claim Zone A. The measured zone mix (A / B / C / outside) from drop tests on surveyed markers will replace this statement.

### 7.4 Drop-accuracy test plan (PENDING)

| Test | Method | Output |
|---|---|---|
| F1 Hover gate | DEL hovering at 3 m over a marker; log position, speed, range | Time to meet the gate; hold-error distribution |
| F2 Release offset | Release over a marker with a known position (no geotag) | Hover + fall + bounce error alone; release delay check |
| F3 Full chain | INS-01 geotags dummies; DEL drops on those geotags | Kit-to-dummy distance: median, 95th percentile, zone mix |
| F4 Wind | Repeat F2 and F3 in different winds | Drift vs wind; release-offset table |

Separating F2 (our delivery error) from F3 (full chain) tells us whether to spend effort on the geotag or on the delivery drone.

---

## Evidence status

| Item | Status | Note |
|---|---|---|
| Kit count, sizes, zones, marks arithmetic | CALCULATED | From the PS1 rules |
| DEL masses 5.414 / 4.414 kg, 902 g hover per motor | CALCULATED | BOM-based, not weighed; thrust curve unverified |
| Gravity magazine + 2-stage escapement, servo torque check | DESIGNED / CALCULATED | Mechanism to be built and bench-tested |
| Delivery sequence, release gate, kit ledger | BUILT-SIMULATED (core logic) | Exists in our GCS software and passes our software simulation on an earlier fleet configuration; 3-drone adaptation being coded now |
| Lua break-beam script on the Cube Orange+ | DESIGNED | To be written and bench-tested |
| Free-fall time, drift, wind and release-delay examples | CALCULATED | Drag inputs ASSUMED |
| Hover / release error 0.4-0.8 m, 25 s service time | ASSUMED | To be replaced by hover logs |
| 50 consecutive single-box releases (B1) | PENDING TEST | Bench target |
| Drop accuracy and zone mix (F1-F4) | PENDING TEST | No numbers claimed |
| Any delivery flight | PENDING TEST | Nothing is flight-tested |

---

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| Geotag error dominates | Kits land in Zone B or C, or outside 3 m | Multi-frame averaging and calibration on INS-01; F2 vs F3 tests show where the error comes from |
| Double release or jam | Two kits on one survivor, or none | 2-stage escapement; B1 target of 50 single releases; UNKNOWN never re-fired |
| Beam misses a box or false-triggers | Wrong count | B2 test; window timing from bench; UNKNOWN state flagged |
| Servo carrying load or browning out | Gate opens or Cube resets | Load-bearing latches; latches hold unpowered; dedicated servo BEC |
| Gate never met in gusty wind | Delay or no drop | Timeout with one re-approach; wider second-attempt gate decided from hover data |
| Wind drift of the falling box | Up to about 0.25 m at 5 m/s | EKF wind estimate shifts the release point upwind; F4 calibration |
| Bounce or slide after a 7.7 m/s impact | Kit ends outside the zone it landed in | Packaging that absorbs impact; B4 drop tests on several surfaces |
| Release steps sent over telemetry | Step timing varies with link latency | Measure in B5; option to sequence the escapement steps locally in the Lua script |
| DEL loaded mass above the ~5 kg frame guidance (413.8 g, 8.3%) | Less thrust margin | Weigh the aircraft; thrust-stand test; trim mass before flight testing |

---

## Likely judge questions

1. Will your kits land in Zone A?
   We do not claim it. The drop point comes from the INS-01 geotag, which is limited mainly by GNSS (about 2 m class). Our release gate keeps hover, fall and wind errors to well under 1 m in our calculations. The zone mix will be measured on surveyed markers.

2. How do you guarantee exactly one box per release?
   The 2-stage escapement locks the rest of the stack before the exit opens, so one cycle can only free one box. The break-beam confirms each exit. Our bench target is at least 50 consecutive single-box releases before flight; this test is pending.

3. What if the beam does not see the box?
   The kit is marked UNKNOWN and is never re-fired automatically, because a second release could put two kits on one survivor. The event is logged and shown on the GCS.

4. Why no camera on the delivery drones to correct the final position?
   We kept the delivery drones simple: no companion computer, and every step commanded by the GCS. All AI is on INS-01. If tests show the geotag error is too large, close-range visual correction or better GNSS are options, but they are not part of the current design.
