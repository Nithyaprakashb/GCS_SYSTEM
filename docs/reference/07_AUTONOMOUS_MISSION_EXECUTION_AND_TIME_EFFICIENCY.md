# Autonomous Mission Execution and Time Efficiency — 30 marks

**NIDAR 2026 · Problem Statement 1 · Phase 4A design review · Rubric row 7**
**Fleet:** INS-01 inspection quad + DEL-01 and DEL-02 delivery hexas, run from one offline GCS
**Status in one line:** the mission logic and time budget are designed and calculated, the GCS core runs in simulation, and nothing has flown yet.

## Key message

After the organiser KML is loaded and START MISSION is pressed, the three drones plan, search, assign, deliver, return and land with zero manual inputs, and the calculated mission time is about 14–15 min against the 30-min cap.

---

## Slide plan

### Slide 1 — "One file, one button: the autonomous mission chain"
- Before flight the operator does only what the rules allow: load the KML and press START MISSION.
- The GCS builds the INS-01 search (9 lanes, 12 m AGL, 8 m/s) and uploads the KML geofence to all three drones.
- Automatic pre-flight checks run for each role. START MISSION stays locked until every check on every drone is green.
- INS-01 searches, confirms each person over 3 frames and geotags them. DEL-01 and DEL-02 wait disarmed on the pad.
- Each confirmed survivor goes to the delivery drone with the smallest ETA that still has a free kit.
- All drones return and land one after another. The manual-input counter should read 0.
- *Suggested visual:* the flow diagram from Section 3, with the two allowed human actions in a different colour.

### Slide 2 — "Search and delivery run at the same time"
- INS-01 covers the whole 10 ha alone: 9 lanes x 500 m with a 23.7 m effective swath, in 10.2 min.
- A delivery drone launches only when it has a survivor, so no battery is spent hovering while it waits.
- The drones fly in three layers: INS-01 searches at 12 m, DEL-01 transits at 18 m and DEL-02 at 22 m.
- A DEL drone descends to 3 m only when no other drone is within 20 m of the drop point.
- It hovers until steady, releases one kit, gets an IR break-beam confirmation, then flies to its next target or returns (RTL).
- Kits are pooled (5 + 5), so the fleet can serve up to 10 survivors wherever they are.
- *Suggested visual:* the Gantt chart from Section 8.3, one row per drone, 0–30 min axis.

### Slide 3 — "Time budget: about 14–15 min against a 30-min cap"
- Design estimate: 0.5 + 10.2 + (1.0 to 2.5) + (1.5 to 2.0) = 13.2 to 15.2 min, which we quote as about 14–15 min.
- Conservative case, where all 5 drops of one DEL drone happen after the search ends: 14.8 min.
- Fallback, if detection is weak at 12 m and we search at 10 m AGL: about 16.5–17 min.
- Combined bad case (10 m search, 35 s per kit, 8 m/s transit): 18.2 min, still 11.8 min under the cap.
- Energy: 15 min in the air uses about 32 % (INS-01) and 40 % (DEL) of the assumed usable energy. These figures come from an unverified thrust curve.
- The 15-min bonus is possible, but we will claim it only after timing a full rehearsal.
- *Suggested visual:* horizontal bars for the four cases, with vertical lines at 15 min and 30 min.

---

## 1. Rules that drive the design

| NIDAR PS1 rule | What the design does |
|---|---|
| 10 ha zone given as a KML file during a 5-min setup | The GCS imports the KML, then builds, uploads and checks the plan automatically |
| Up to 10 survivors | 10 kits in total, 5 on DEL-01 and 5 on DEL-02, shared between both drones |
| 30-min cap; 50-mark bonus for finishing in 15 min or less | Delivery overlaps the search and DEL drones launch only when needed. Calculated time: about 14–15 min |
| -50 per manual input (only load mission, start and abort / emergency recall are allowed) | After the KML is loaded, the GCS offers only START MISSION and the emergency controls. Everything else is locked and logged |
| -20 per geofence breach | The KML polygon is the autopilot fence on all three drones. Lane ends sit 3 m inside it, and the GCS warns when a drone is within 2 m of it |
| Offline only | No internet. Planning, map, allocation and logs run on the GCS laptop; the AI runs on INS-01 |
| One GCS showing live video from every drone at the same time | Three analog 5.8 GHz feeds appear side by side on the same GCS |
| 12 ft launch / landing pad | Pad interlock: only one drone takes off or lands at a time |

## 2. Roles in the autonomous mission

| | INS-01 | DEL-01 | DEL-02 | GCS |
|---|---|---|---|---|
| Airframe | Quad | Hexa | Hexa | Laptop, offline |
| Job | Search the whole polygon; detect, confirm and geotag survivors | Deliver kits to the survivors it is given | Deliver kits to the survivors it is given | Plan, check, assign, command every delivery step, monitor, log |
| Onboard control | Cube Orange+ (ArduPilot AUTO mission) + Pi 5 + Hailo-8 | Cube Orange+ (GUIDED, commanded by the GCS) + Lua break-beam script | Same as DEL-01 | Python server + Electron app |
| Mission sensors | Pi Camera 3 Wide on a nadir gimbal | TFmini-S rangefinder, IR break-beam | TFmini-S, IR break-beam | — |
| Payload | None | 5 kits in a 2-stage escapement | 5 kits in a 2-stage escapement | — |
| Working altitude | 12 m AGL (search) | 18 m (transit) | 22 m (transit) | — |
| RTL altitude | 25 m | 18 m | 22 m | — |
| At START | Takes off | Stays disarmed on the pad | Stays disarmed on the pad | Logs START |
| Live video | RunCam Nano 2 + TX800 | RunCam Nano 2 + TX800 | RunCam Nano 2 + TX800 | 3 panels |

The DEL drones carry no AI computer. The GCS sends every delivery step over MAVLink. The Lua script on the Cube Orange+ only reads the break-beam and reports the result.

## 3. End-to-end autonomy flow

```text
 SETUP (inside the 5-min window, all drones on the pad)              HUMAN INPUT
 +---------------------------------------------------------------+
 | 1. Load organiser KML                                         | <-- load mission (allowed)
 | 2. Auto plan: INS-01 lanes over the whole polygon             |
 |    (9 lanes, 12 m AGL, 8 m/s, lane ends 3 m inside)           |
 | 3. Upload INS-01 mission; upload geofence to all 3 drones     |
 | 4. Auto pre-flight checks per role (INS / DEL)                |
 +-------------------------------+-------------------------------+
                                 | all green
                                 v
                       [ START MISSION ]  <------------------------ start (allowed)
                                 |
 FLIGHT (no human input)         v
 INS-01:  take off -> lane 1 ... lane 9 -> RTL at 25 m -> land
             |
             | person seen in 3 frames -> geotag = mean of 3 -> merge if within 5 m
             v
 GCS:     SURVIVOR CONFIRMED -> pick the DEL drone with the smallest ETA
          that has a free kit, enough battery and a healthy link
             |
             v
 DEL-x:   arm when the pad is clear -> climb to 18 m (DEL-01) / 22 m (DEL-02)
          -> fly to geotag -> wait until no other drone is within 20 m
          -> descend to 3 m (TFmini-S) -> hover-verify -> release 1 kit
          -> break-beam confirms -> next queued survivor, or RTL
             |
             v
 END:     search finished and all queues empty -> all RTL
          -> staggered landing (one drone on the pad at a time) -> logs saved

 Always available, safety only: EMERGENCY RECALL (all RTL) / ABORT (land)    (allowed)
```

### Phase table

| # | Phase | Starts when | Decided by | What happens | Human inputs |
|---|---|---|---|---|---|
| 1 | Load | KML file available | Operator | KML imported, area shown | 1 (allowed: load mission) |
| 2 | Plan + upload | KML is valid | GCS | Lanes and fence built, uploaded, acknowledged | 0 |
| 3 | Pre-flight | Uploads confirmed | GCS | Per-role checks; START unlocks when all pass | 0 |
| 4 | Start | All checks green | Operator | START MISSION | 1 (allowed: start) |
| 5 | Search | START | INS-01 autopilot (AUTO) + Pi 5 | Lanes flown; people detected, confirmed and geotagged | 0 |
| 6 | Assign | A survivor is confirmed | GCS | Smallest-ETA rule; one kit reserved | 0 |
| 7 | Deliver | Assignment made | GCS commands the DEL drone in GUIDED | Launch, transit, descend, release, confirm | 0 |
| 8 | Recover | Search done and all queues empty | GCS | All RTL; staggered landing | 0 |
| 9 | Record | All drones landed | GCS | Mission folder saved | 0 |

**Penalised manual inputs planned: 0.**

## 4. Automatic mission planning from the KML

### 4.1 Planner steps
1. Read the KML polygon. Check that it is closed and does not cross itself, and show its area (about 10 ha).
2. Run the lanes along the long axis of the polygon, which gives the fewest lanes and turns.
3. Space the lanes no wider than the usable swath (23.7 m at 12 m AGL).
4. Clip every lane to the polygon and pull both lane ends 3 m inside the boundary.
5. Build the INS-01 AUTO mission: take off, fly the lanes at 8 m/s, RTL.
6. Build the fence for all three drones: the KML polygon plus an altitude limit, with RTL as the action.
7. Upload over MAVLink and wait for acknowledgement. Read the plan back and compare it item by item; any mismatch keeps START locked.
8. Draw the plan on the map with its time estimate before START.

### 4.2 Search geometry (500 m x 200 m field model)

| Quantity | Value | How it is obtained | Label |
|---|---|---|---|
| Search altitude / speed | 12 m AGL / 8 m/s | Design choice | DESIGNED |
| Camera horizontal field of view | 102 deg | Pi Camera 3 Wide | ASSUMED |
| Full across-track footprint | 29.6 m | 2 x 12 x tan(51 deg) | CALCULATED |
| Effective swath (80 % usable) | 23.7 m | 0.8 x 29.6 m | ASSUMED share, CALCULATED value |
| Lanes across 200 m | 9 | ceil(200 / 23.7) | CALCULATED |
| Lane spacing | 22.2 m | 200 / 9, so neighbouring lanes overlap by 1.5 m | CALCULATED |
| Outer lane to long edge | 11.1 m | Less than half the swath (11.85 m), so the edge strip is seen | CALCULATED |
| Along-track footprint | about 15.9 m | 2 x 12 x tan(33.5 deg), assuming a 67 deg vertical FOV | CALCULATED |
| Lane-end inset | 3 m | The camera still sees about 4.9 m past each lane end | CALCULATED |
| Path length | 4,678 m | 9 x 500 m + 8 x 22.2 m | CALCULATED |
| Turns | 8 x 3 s | Turn allowance | ASSUMED |
| Search time | 10.15 min, quoted as 10.2 min | 4,678 m / 8 m/s + 24 s | CALCULATED |
| Pixels across a 0.5 m person | 21.6 px | 518.26 px x 0.5 m / 12 m (1280 px image width) | CALCULATED |
| Motion blur at 1/1000 s | 0.8 cm (0.35 px) | 8 m/s x 0.001 s | CALCULATED |
| Time a person stays in view | about 2.0 s | 15.9 m / 8 m/s. At 5 frames/s or more that is at least 10 frames, enough for 3-frame confirmation | CALCULATED (frame rate ASSUMED) |

### 4.3 Why 12 m, and the fallback

| Altitude (at 8 m/s) | Swath | Lanes | Search time | Pixels on a 0.5 m person | Use |
|---|---|---|---|---|---|
| 10 m | 19.8 m | 11 | 12.3 min | 25.9 px | Fallback if detection at 12 m is weak |
| **12 m** | **23.7 m** | **9** | **10.2 min** | **21.6 px** | **Baseline** |
| 15 m | 29.6 m | 7 | 8.0 min | 17.3 px | Not used: below the ~20 px planning floor |

12 m is the highest altitude that keeps a person above the ~20 px planning floor, so it gives the shortest search the detector should handle. A detection test on dummies will make the final choice, not this table.

## 5. From detection to survivor report (INS-01)

1. The gimbal holds the Pi Camera 3 Wide straight down, and frames stream to the Pi 5.
2. The person detector (YOLOv8n) runs on the Hailo-8.
3. A person becomes a survivor only after **3 frames** whose geotags fall close together. The reported position is the mean of those 3 geotags.
4. A new confirmation within **5 m** of a known survivor updates that survivor instead of creating a duplicate. This merge runs on the Pi and again on the GCS.
5. The survivor report (ID, latitude, longitude, confidence, geotag error, time) travels Pi -> Cube Orange+ serial port -> RFD868x -> GCS as a MAVLink message.
6. INS-01 never stops or circles over a survivor; it keeps flying its lanes. Detections therefore do not make the search longer.

## 6. Automatic delivery allocation (GCS)

### 6.1 Rule
- A **candidate** is a DEL drone with a healthy link, no active failsafe, a free (unreserved) kit, and enough battery for its current queue, the new survivor and an RTL with reserve.
- **ETA** = time to finish its current queue + launch time if it is on the pad + flight time from the end of its queue to the survivor.
- The candidate with the **smallest ETA** gets the survivor. If the ETAs are within 2 s of each other, the drone with more free kits wins, then the lower ID.
- The GCS reserves one kit at the moment of assignment, so two survivors can never be promised the same kit.
- If there is no candidate, the GCS marks the survivor UNSERVED and logs it.

### 6.2 Pseudocode

```text
on survivor_confirmed(s):
    candidates = [d in {DEL-01, DEL-02}
                  if d.link_ok and not d.failsafe
                  and d.free_kits > 0
                  and d.energy_ok(queue + s + RTL + reserve)]
    if candidates is empty:
        s.status = UNSERVED; log(s); return
    for d in candidates:
        d.eta = d.time_to_finish_queue()
              + (LAUNCH_TIME if d.on_pad else 0)
              + distance(d.queue_end_position, s.position) / 10 m/s
    best = min(candidates, key = (round_to_2s(d.eta), -d.free_kits, d.id))
    best.queue.append(s); best.free_kits -= 1        # kit reserved now
    s.status = ASSIGNED; s.owner = best.id
    if best.on_pad: request_launch(best)             # waits for the pad interlock
    log_decision(s, best, eta_of_every_candidate)
```

### 6.3 Why pooled kits matter
Kits are not tied to one half of the field. For example, with 7 survivors in the west half and 3 in the east, DEL-01 serves 5 western survivors. Once it has no free kit it stops being a candidate, and DEL-02 takes the remaining 2 western and 3 eastern survivors. All 10 are served and no kit is left unused.

## 7. Delivery execution (DEL-01 / DEL-02)

### 7.1 State machine

```text
 STANDBY (disarmed on pad)
    | assigned + pad clear + kits / battery / link OK
    v
 LAUNCHING (arm, GUIDED take-off to 18 m DEL-01 / 22 m DEL-02)
    v
 TRANSIT (GUIDED go-to survivor geotag, 10 m/s)
    v
 HOLD ABOVE TARGET --- another drone within 20 m of the drop point? --- yes: wait
    | clear
    v
 DESCEND to 3 m AGL (TFmini-S)
    v
 HOVER-VERIFY (position, speed and height steady)
    |  not steady before timeout -> climb, re-approach once,
    |  then release only if inside the 1 m ring
    v
 RELEASE (GCS sends DO_SET_SERVO to the 2-stage gate: exactly one kit)
    v
 CONFIRM (Lua script reads the IR break-beam in a short window)
    |  beam broken -> DELIVERED          no beam -> UNKNOWN (never re-fired)
    v
 CLIMB to transit altitude -> next queued survivor (back to TRANSIT)
    | queue empty
    v
 RETURNING (RTL at own altitude) -> LANDING (pad interlock) -> STANDBY
```

### 7.2 Timing per delivery

| Step | Time | Basis | Label |
|---|---|---|---|
| Transit leg | 15–30 s | 150–300 m at 10 m/s | ASSUMED speed, CALCULATED time |
| Wait for the 20 m clearance | 0–5 s typical | INS-01 at 8 m/s crosses a 40 m circle in 5 s | CALCULATED |
| Service: descend to 3 m, hover-verify, release, confirm, climb | 25 s | Planning allowance | ASSUMED |
| One kit | 40–55 s | Leg + service | CALCULATED |
| 5 kits back to back | about 200 s (3.3 min) | 5 x (15 s + 25 s) | CALCULATED |
| Final return + descent + landing | about 45 s (0.75 min) | 15 s leg + 30 s descent and landing | ASSUMED |
| Arm + climb at launch | about 10 s per sortie | Not counted in the 200 s figure | ASSUMED |

**Check on the 25 s service time:** at ArduPilot's default speeds (1.5 m/s down, 2.5 m/s up), the vertical moves alone take about 16 s for DEL-01 (15 m each way) and 20 s for DEL-02 (19 m each way). That leaves about 4–9 s to hover-verify, release and confirm. The allowance is tight, so it is the first number we will measure.

## 8. Time budget

### 8.1 Inputs

| Input | Value | Label |
|---|---|---|
| Field model | 500 m x 200 m (10 ha) | ASSUMED |
| INS-01 take-off and reach lane 1 | 0.5 min | ASSUMED |
| Search, 9 lanes at 8 m/s with 3 s turns | 10.2 min | CALCULATED |
| DEL transit speed | 10 m/s | ASSUMED |
| Service per kit | 25 s | ASSUMED |
| Last drop after the last detection | 1.0–2.5 min | CALCULATED. About 1 min if a DEL drone is free; about 2.5 min if the survivor waits behind two queued drops |
| RTL and staggered landing of all 3 drones | 1.5–2.0 min | ASSUMED |
| Wind | Not modelled | — |

### 8.2 Mission time cases

| Case | Arithmetic (min) | Total | Against 15 min | Against 30 min |
|---|---|---|---|---|
| **Design estimate** | 0.5 + 10.2 + (1.0 to 2.5) + (1.5 to 2.0) | 13.2–15.2, quoted as **about 14–15 min** | At the line | 14.8–16.8 min to spare |
| **Conservative**: all 5 drops of one DEL drone after the search ends | 0.5 + 10.2 + 3.3 + 0.75 | **14.8 min** | 0.2 min under | 15.2 min to spare |
| **Fallback**: search at 10 m AGL | 0.5 + 12.4 + delivery tail + return | **about 16.5–17 min** (conservative arithmetic gives 17.0) | Over | about 13 min to spare |
| **Combined bad case**: 10 m, 35 s per kit, 8 m/s transit | 0.5 + 12.4 + 4.5 + 0.8 | **18.2 min** | Over | 11.8 min to spare |

The conservative case counts only the last drone's return (0.75 min). By then INS-01 and the other DEL drone have already landed while the last drops were being made. The design-estimate range uses a fuller 1.5–2.0 min for the end of the mission.

### 8.3 Timeline (design estimate; drop times are only an example)

```text
             0         5         10        15        20        25        30   (min)
             |----+----|----+----|----+----|----+----|----+----|----+----|
INS-01       T=====================RL
DEL-01       ......>dd<.....>d<....>dd<L
DEL-02       ........>d<.....>dd<....>dd<L
                                           ^ 15-min bonus line           ^ 30-min cap

 1 character = 0.5 min    T take-off    = search lanes    . on pad, disarmed
 > launch + transit    d one kit dropped    < return    R RTL    L landing (one drone at a time)
```

What the chart shows: deliveries are spread across the search, so only the last 1–2.5 min of delivery comes after INS-01 finishes. In this example the last landing is at about 14.5 min.

### 8.4 Sensitivity (conservative case, one change at a time)

| Change | Effect | Conservative total |
|---|---|---|
| None (baseline) | — | 14.8 min |
| Arm + first climb counted separately (about 10 s) | +0.2 min | 14.9 min |
| DEL transit at 8 m/s instead of 10 m/s | +0.4 min | 15.2 min |
| Service 35 s per kit instead of 25 s | +0.8 min | 15.6 min |
| One extra search lane (because of the polygon shape) | +1.1 min | 15.9 min |
| Search speed 7 m/s instead of 8 m/s | +1.3 min | 16.1 min |
| Search at 10 m AGL (fallback) | +2.2 min | 17.0 min |
| Combined: 10 m + 35 s per kit + 8 m/s transit | +3.4 min | 18.2 min |

The search altitude and speed move the total the most, so they are the first values to fix by flight test. No single change, and not even the combined bad case, comes near the 30-min cap.

### 8.5 Energy margin (CALCULATED on an unverified reference thrust curve)

| | INS-01 | DEL-01 / DEL-02 (loaded) |
|---|---|---|
| Hover power | 385 W | 476 W |
| Mission-profile power | 438 W | 546 W |
| Usable energy | 341.88 Wh (assumed 0.70 x 488.4 Wh nameplate) | 341.88 Wh (same assumption) |
| Mission-profile endurance | 46.8 min | 37.6 min |
| Planned time in the air | about 12 min (take-off to landing) | about 4 min (5 kits back to back) to about 8–10 min (5 separate sorties) |
| Energy if in the air for the full 15 min | about 110 Wh (32 %) | about 136 Wh (40 %) |

Each DEL drone also saves energy by waiting disarmed on the pad until it gets its first survivor.

## 9. Zero manual inputs: how it is enforced

| Control | When it is available | Penalised? |
|---|---|---|
| Load mission (KML) | Setup | No (allowed) |
| START MISSION | Only when every pre-flight check is green | No (allowed) |
| EMERGENCY RECALL (all RTL, two clicks within 4 s) / ABORT (land) | During flight | No (allowed), used only for safety |
| Mode changes, waypoint edits, manual kit release, re-tasking | Not offered: locked in the GCS | Would be, so the GCS does not allow them |

- The GCS logs every operator action with its time in `operator_actions.json`, and the top bar shows the manual-input counter.
- If the flight mode changes without a GCS command (for example an RC takeover by a safety pilot), the GCS flags it in the timeline, so the input count can be audited.

## 10. Where the time is saved

| Design choice | Time effect |
|---|---|
| One searcher with a wide nadir camera at 12 m | Only 9 lanes and 8 turns: 10.2 min for 10 ha |
| Lanes along the long axis of the polygon | Fewest turns (each costs about 3 s plus the lane shift) |
| INS-01 never stops for a survivor | Detections do not make the search longer |
| Delivery in parallel with the search | Only the last 1–2.5 min of delivery comes after the search |
| DEL drones launch only when assigned | No hovering while waiting; battery is kept for delivery |
| Smallest-ETA assignment that counts each drone's queue | The drone that can arrive first goes, and queues stay short |
| Pooled kits | Neither drone runs out of kits while the other lands with spares |
| Separate transit layers (18 m / 22 m) | DEL drones never wait for each other in transit; only the short 20 m descent check remains |
| Automatic plan, upload and checks | Setup fits the 5-min window (target, still to be timed) |
| Different RTL altitudes | All drones can return at once; only the final descents are taken in turn |

---

## Evidence status

| Item | Status | What closes it |
|---|---|---|
| Search geometry: 9 lanes, 23.7 m swath, 10.2 min | CALCULATED (500 x 200 m model; camera FOV and the 80 % usable swath are ASSUMED) | Plan on the real KML; time the search in SITL and in flight |
| Delivery timing: 10 m/s, 15–30 s legs, 25 s per kit, 200 s for 5 kits | ASSUMED inputs, CALCULATED totals | Time descent, hover-verify and release in flight |
| Mission time about 14–15 min; conservative 14.8 min; fallback about 16.5–17 min | CALCULATED | Timed full rehearsal |
| Endurance 46.8 / 37.6 min and energy margin | CALCULATED on an unverified thrust curve; usable energy ASSUMED | Thrust-stand test and flight logs |
| Autonomy flow, smallest-ETA allocation, delivery state machine, pad interlock | DESIGNED | Finish the code, then run three SITL instances |
| KML import, lane planning with the 3 m inset, mission and fence upload with acknowledgement, START / RECALL lock, manual-input counter | BUILT-SIMULATED (GCS core, run on an earlier fleet configuration) | Re-run with the three-drone fleet in SITL |
| Plan read-back and item-by-item comparison | DESIGNED | SITL |
| Detection, 3-frame confirmation and geotag on INS-01 | DESIGNED | Detector benchmark on the Hailo-8; geotag test on ground markers |
| Lua break-beam script and 2-stage escapement | DESIGNED | Bench test |
| Three-drone SITL run; any flight | PENDING TEST | — |
| 15-min bonus | Not claimed | PENDING TEST (timed rehearsal) |

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| Detection is weak at 12 m (21.6 px per person, which halves to about 10.8 px if the detector runs at 640 px input) | Survivors missed or found late | Run the detector at 1280 px width or tile the frame; test on dummies; fall back to 10 m AGL (+2.2 min, still about 17 min) |
| 8 m/s is too fast for 3-frame confirmation | Confirmations missed | A person is in view for about 2 s, so 5 frames/s or more gives at least 10 frames. Benchmark the Hailo frame rate; slow to 7 m/s if needed (+1.3 min) |
| The real KML is not a 500 x 200 m rectangle | More lanes and a longer search | The planner recomputes from the real polygon and shows the estimate before START. Each extra lane adds about 1.1 min |
| Wind | Slower upwind lanes; drift at the 3 m drop height | ETA uses measured ground speed; hover-verify before release; test in wind; the 30-min cap leaves a large margin |
| Service takes longer than 25 s | Conservative case goes above 15 min | Measure in SITL and in flight; tune descent speed; claim the bonus only after timing |
| Telemetry dropout on the RFD868x network | Delivery steps pause, and a long loss triggers RTL | Check the link budget and antenna placement; tune stream rates for three drones; show a link light per drone |
| INS-01 is the only searcher | If it fails, no new survivors are found | Strict pre-flight AI check. Confirmed survivors are still delivered, and unsearched lanes show on the heatmap |
| Three drones share one 12 ft pad | Launches and landings have to wait their turn | Pad interlock; DEL drones launch only when assigned; different RTL altitudes |

## Likely judge questions

**Q1. Have you measured the 14–15 min?**
No. It is calculated from the assumptions listed above. What we claim is that the plan is well inside 30 min, and we will claim the 15-min bonus only after a timed full rehearsal.

**Q2. What if survivors are found in the last lane?**
That is our conservative case. Even if all five drops of one DEL drone happen after the search, the total is 14.8 min. The last drop comes about 1–2.5 min after the last detection.

**Q3. How do you guarantee zero manual inputs?**
After the KML is loaded, the GCS offers only START MISSION and the emergency controls, and nothing else can be clicked. Every press is logged in `operator_actions.json` and counted on screen.

**Q4. Why do the delivery drones wait on the ground instead of hovering?**
Hovering costs about 476 W per DEL drone (calculated on an unverified curve) and gains nothing. On the pad they keep their battery for delivery, and the ETA already includes the launch time.
