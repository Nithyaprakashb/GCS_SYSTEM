# Multi-Drone Collaboration and Task Allocation — 30 marks

Team ADRODS-X · NIDAR PS1 · Phase 4A design review · Rubric row 4

## Key message

One inspection drone (INS-01) searches and geotags while two delivery hexacopters (DEL-01, DEL-02) deliver in parallel, and a single ground-station allocator sends each confirmed survivor to the delivery drone with the shortest estimated time that still has a free kit, so any split of 10 survivors is served with 10 pooled kits and no manual input.

---

## Slide plan

### Slide 1: Three drones, three roles, one decision-maker

- INS-01 (quad) searches the whole 10 ha at 12 m AGL, runs the AI and geotags survivors. It carries no kits.
- DEL-01 and DEL-02 (hexas) carry 5 kits each (10 in total). They have no AI computer; the GCS commands every step.
- The GCS is the single writer: survivor list, allocation, kit ledger and airspace clearances are decided in one place.
- Search and delivery run in parallel: INS-01 keeps searching while the DEL drones deliver.
- DEL drones wait disarmed on the pad until their first assignment, saving battery.
- Operator actions after START MISSION: none planned (EMERGENCY RECALL only for safety). Each manual input costs -50.

Suggested visual: the data-flow diagram in Section 1 (three drones around the GCS, arrows labelled "survivor reports", "commands", "status", "video").

### Slide 2: Allocation rule and pooled kits (7 west + 3 east example)

- On each confirmed survivor: ETA = distance(drone position or last queued target, survivor) / 10 m/s + 25 s x queued count.
- Candidates: link OK, not returning for battery/failsafe/recall, and free kits (available - committed) > 0.
- Smallest ETA wins; tie -> more free kits; still tied -> lower ID (repeatable).
- Kits are pooled, not tied to zones: when a drone runs out it simply stops being a candidate.
- Worked example (7 west + 3 east): DEL-01 serves 5, DEL-02 serves 5; every kit delivered about 1 to 2 min after confirmation in the timeline model.
- Duplicate sightings (within 5 m) are merged and never get a second kit.

Suggested visual: the 10-row assignment table from Section 4.1 with the winning ETA highlighted, plus a small field map with S1 to S10 coloured by drone.

### Slide 3: Safe sharing of the airspace and recovery from failures

- Altitude layers: INS-01 search 12 m, DEL-01 transit 18 m, DEL-02 transit 22 m, drops at 3 m.
- A DEL drone descends only when INS-01 and the other DEL are at least 20 m horizontally from the drop point.
- Pad interlock: one take-off or landing at a time (staggered launch and landing).
- 10/0 cluster: both DEL drones work the same cluster; the 20 m rule makes them take turns.
- DEL failure or low battery: its undelivered queue is reassigned to the other DEL if it has free kits; otherwise survivors stay geotagged and flagged UNSERVED.
- Timeline model: search ends 10:39, last kit 11:51, all landed about 13:03 (planning figure 14-15 min, cap 30 min).

Suggested visual: side-view altitude-layer diagram (Section 5) next to the 30-second-per-character Gantt bar (Section 7).

---

## 1. Roles and data flow

| Drone | Role | Key hardware | Carries kits | Decides on its own |
|---|---|---|---|---|
| INS-01 | Search, AI detection, geotag | 650 mm quad, Pi 5 + Hailo-8 (26 TOPS), Pi Camera 3 Wide on 2-axis nadir gimbal, Cube Orange+, M10 GNSS, RunCam Nano 2 + TX800 | No | Only detection and confirmation of survivors (3 frames, mean confidence >= 85%) |
| DEL-01 | Delivery | 900 mm hexa, Cube Orange+, M10 GNSS, TFmini-S lidar, 5-box magazine + 2-stage escapement, IR break-beam (Lua on Cube), RunCam Nano 2 + TX800 | 5 x 200 g | Nothing mission-level; flies GCS commands, keeps its own ArduPilot failsafes |
| DEL-02 | Delivery | Same as DEL-01 | 5 x 200 g | Same as DEL-01 |
| GCS | Mission brain | Offline laptop, RFD868x ground radio, 3 video receivers | - | Survivor registry, allocation, kit ledger, descent clearance, pad interlock, logging |

```text
                       +------------------------------------------------------+
                       |                GCS  (single writer)                  |
                       |  survivor registry (5 m merge)   allocator (ETA)     |
                       |  kit ledger (per kit)            descent clearance   |
                       |  pad interlock                   decision log        |
                       |  mission map + 3 live video panels                   |
                       +------------------------------------------------------+
                          ^  survivor reports            |  GUIDED go-to, descent
                          |  (ID, lat/lon, confidence,   |  clearance, release
                          |   time) + position, health   |  command, RTL
                          |                              v
        MAVLink 2 over RFD868x (each drone has its own system ID)
             |                           |                             |
       +-----------+              +-------------+               +-------------+
       |  INS-01   |              |   DEL-01    |               |   DEL-02    |
       | search    |              | transit 18 m|               | transit 22 m|
       | 12 m AGL  |              | drop at 3 m |               | drop at 3 m |
       | YOLOv8n + |              | TFmini-S,   |               | TFmini-S,   |
       | geotag    |              | gate, beam  |               | gate, beam  |
       +-----------+              +-------------+               +-------------+
             \___________ 5.8 GHz analog video, 3 separate channels ________/
                               (display and recording only)
```

Information flows:

| From -> To | Content | Link |
|---|---|---|
| INS-01 Pi 5 -> GCS | Confirmed survivor: ID, latitude, longitude, mean confidence, frames used, time | Pi -> Cube serial -> RFD868x (MAVLink TUNNEL messages) |
| All drones -> GCS | Position, altitude, speed, battery, mode, link health | MAVLink 2 telemetry |
| GCS -> DEL | GUIDED go-to at transit altitude, descent clearance, release command, next target or RTL | MAVLink 2 |
| DEL Cube Lua script -> GCS | Break-beam result after each release command | MAVLink 2 |
| GCS -> all | Geofence, INS-01 search lanes (built from the KML in the 5-min setup), EMERGENCY RECALL | MAVLink 2 |
| All drones -> GCS | Live analog video | 5.8 GHz, one channel per drone; flight never depends on it |

Why a central allocator:

- DEL drones have no companion computer, so the GCS is the only place that sees all three drones and all survivors.
- One writer means no two drones can claim the same survivor or the same kit, without any drone-to-drone negotiation.
- Every decision is logged with the ETA of every candidate, so the judges and the team can replay and explain each choice.
- If a DEL drone loses the GCS link it returns home (ArduPilot failsafe). A kit is released only on a GCS command, so link loss can never cause a release.

---

## 2. Mission flow (who does what, when)

```text
 Setup (5 min, before clock): import KML -> geofence + 9 search lanes -> upload -> auto pre-flight checks
        |
 START MISSION
        |
 INS-01: take off -> lawnmower search at 12 m, 8 m/s ------------------------------> RTL, land
        |                \ survivor confirmed (3 frames, >= 85%)          \ (repeats up to 10 times)
        |                 v                                                v
 GCS:   merge within 5 m? --yes--> update existing survivor (no new kit)
                          --no---> allocate (ETA rule) -> reserve 1 kit -> command DEL
        |
 DEL-0x: (on pad, disarmed) -> staggered launch -> transit layer -> wait for descent clearance
         -> descend to 3 m -> release gate -> one box -> beam confirm -> next target or RTL
```

| Phase | INS-01 | DEL-01 / DEL-02 | GCS |
|---|---|---|---|
| Setup (5 min) | On pad, powered | On pad, 5 kits each | KML import, geofence, lanes, uploads, pre-flight checks |
| 0:00-0:30 | Take off, reach lane 1 | Disarmed on pad | Logs launch |
| 0:30-10:39 | 9 lanes, AI on every frame | Launch on first assignment; deliver; re-tasked in the air or land and wait | Merge, allocate, clear descents, update ledger |
| After search | RTL and land (pad interlock) | Finish queue, RTL, land | Staggered landings; save logs and video |

---

## 3. The allocation rule

### 3.1 Rule

For each newly confirmed survivor S, for each delivery drone d:

```text
ETA_d = distance(start_d, S) / 10 m/s  +  25 s x queued_count_d

start_d = position of the last survivor in d's queue, if the queue is not empty
          d's current position, otherwise (pad, in the air, or returning)
queued_count_d = survivors assigned to d and not yet delivered
```

| Element | Value | Why |
|---|---|---|
| Transit speed | 10 m/s | ASSUMED planning speed for the loaded hexa |
| Service time per queued survivor | 25 s | ASSUMED: descend to 3 m, settle, release, beam check, climb |
| Candidate: link OK | heartbeat within timeout | A drone the GCS cannot command cannot be given work |
| Candidate: not returning for battery / failsafe / recall | flag from telemetry | A returning-for-safety drone must not be re-tasked |
| Candidate: free kits > 0 | kits_available - committed | Prevents over-booking; committed = RESERVED kits |
| Winner | smallest ETA | Nearest in time, including its existing queue |
| Tie | more free kits, then lower ID | Spreads kits; fixed order makes the result repeatable |

Note: a DEL drone that is flying home only because its queue is empty is still a candidate and is re-tasked in the air.

### 3.2 Pseudocode (GCS)

```text
on survivor_confirmed(S):
    if distance(S, any known survivor) <= 5 m:
        merge into that survivor (update mean position); return       # never a second kit
    cands = [d in {DEL-01, DEL-02} where d.link_ok
             and not d.returning_for(battery, failsafe, recall)
             and d.kits_available - d.committed > 0]
    if cands is empty:
        S.status = UNSERVED  (still shown with its geotag); log; return
    for d in cands:
        start = d.queue[-1].pos if d.queue else d.pos
        d.eta = dist(start, S.pos) / 10 + 25 * len(d.queue)
    best = min(cands, key = (d.eta, -(d.kits_available - d.committed), d.id))
    kit = best.ledger.reserve(S)            # AVAILABLE -> RESERVED
    best.queue.append(S); S.status = ASSIGNED; S.owner = best.id
    if best.on_pad: request launch slot (pad interlock)
    else:           re-task in the air (GUIDED go-to)
    log(S, best, ETA of every candidate, reason)
```

### 3.3 Why pooled kits serve any split

- The 10 kits are not tied to halves or zones of the field.
- A drone with no free kit stops being a candidate, so the next survivor automatically goes to the drone that still has kits, wherever it is.
- As long as both DEL drones are working, 10 kits cover any 10 survivors: 7/3, 10/0, 5/5 or any other split.
- With one DEL drone out, the fleet can still deliver: kits already delivered + free kits on the healthy drone.

---

## 4. Worked examples (timeline model)

Model used for all three examples (CALCULATED on ASSUMED inputs, not flight data):

| Input | Value |
|---|---|
| Field | 10 ha modelled as 200 m (east-west) x 500 m (north-south) |
| Search | INS-01 at 12 m, 8 m/s; 9 north-south lanes, 22.2 m apart, flown west to east; 23.7 m usable swath; 3 s per lane change; 30 s launch allowance |
| Detection | Survivor confirmed when INS-01 passes it on the first lane that covers it |
| Pads | Shared launch area 15 m south of the field, mid-edge (ASSUMED) |
| DEL | Launch 20 s, transit 10 m/s, service 25 s, landing 30 s; one pad movement at a time (10 s slot) |
| Queue empty | RTL; re-tasked in the air if a new assignment arrives; otherwise land and wait disarmed |
| Clearance | Descent only when INS-01 and the other DEL are >= 20 m from the drop point |

Coordinates are x = metres east of the west edge, y = metres north of the south edge.

### 4.1 Example 1: 7 survivors in the west half, 3 in the east

| # | Clock | Survivor (x, y) | Half | Lane | ETA DEL-01 | ETA DEL-02 | Assigned | Reason | Free kits after (01 / 02) |
|---:|---|---|---|---:|---|---|---|---|---|
| 1 | 0:40 | S1 (20, 80) | W | 1 | 12.4 s (pad) | 12.4 s (pad) | DEL-01 | Tie on ETA and kits -> lower ID | 4 / 5 |
| 2 | 1:24 | S2 (10, 430) | W | 1 | 60.0 s (35.0 + 25 x 1) | 45.4 s (pad) | DEL-02 | Smaller ETA | 4 / 4 |
| 3 | 2:03 | S3 (45, 300) | W | 2 | 32.0 s (at pad) | 38.5 s (13.5 + 25 x 1) | DEL-01 | Smaller ETA | 3 / 4 |
| 4 | 2:15 | S4 (35, 210) | W | 2 | 34.1 s (9.1 + 25 x 1) | 47.1 s (22.1 + 25 x 1) | DEL-01 | Smaller ETA | 2 / 4 |
| 5 | 3:05 | S5 (60, 150) | W | 3 | 56.5 s (6.5 + 25 x 2) | 17.4 s (flying home) | DEL-02 | Smaller ETA; re-tasked in the air | 2 / 3 |
| - | 3:24 | S3 seen again | W | 3 | - | - | Merged | Within 5 m of S3: no new survivor, no kit | 2 / 3 |
| 6 | 4:00 | S6 (75, 460) | W | 4 | 50.3 s (25.3 + 25 x 1) | 42.7 s (flying home) | DEL-02 | Smaller ETA; re-tasked in the air | 2 / 2 |
| 7 | 4:52 | S7 (85, 40) | W | 4 | 5.7 s (at pad) | 67.0 s (42.0 + 25 x 1) | DEL-01 | Smaller ETA | 1 / 2 |
| 8 | 6:41 | S8 (130, 260) | E | 6 | 27.7 s (pad) | 27.7 s (pad) | DEL-02 | Tie on ETA -> more free kits (2 vs 1) | 1 / 1 |
| 9 | 9:15 | S9 (160, 120) | E | 8 | 14.8 s (pad) | 14.8 s (pad) | DEL-01 | Tie on ETA and kits -> lower ID | 0 / 1 |
| 10 | 10:25 | S10 (185, 390) | E | 9 | Not a candidate (0 free) | 41.4 s (pad) | DEL-02 | Only candidate | 0 / 0 |

Result:

| Drone | Survivors served | West / East | Kits used |
|---|---|---|---|
| DEL-01 | S1, S3, S4, S7, S9 | 4 / 1 | 5 |
| DEL-02 | S2, S5, S6, S8, S10 | 3 / 2 | 5 |

Delivery timeline from the same model:

| Drone | Sortie | Launch | Kits delivered (beam confirmed) | Landed |
|---|---:|---|---|---|
| DEL-01 | 1 | 0:40 | S1 at 1:38 | 2:20 |
| DEL-01 | 2 | 2:20 | S3 at 3:37, S4 at 4:11 | 5:05 |
| DEL-01 | 3 | 5:05 | S7 at 5:56 | 6:31 |
| DEL-01 | 4 | 9:15 | S9 at 10:15 | 11:00 |
| DEL-02 | 1 | 1:24 | S2 at 2:54, S5 at 3:48, S6 at 5:08 (two in-air re-tasks) | 6:25 |
| DEL-02 | 2 | 6:41 | S8 at 7:54 | 8:52 |
| DEL-02 | 3 | 10:25 | S10 at 11:51 | 13:03 |

- Confirmation-to-delivery time: mean 75 s, maximum 117 s (S4, queued behind S3). CALCULATED in the model.
- The 7/3 imbalance does not matter: DEL-01 finishes the west, and the east survivors go to whichever drone has kits.

### 4.2 Example 2: all 10 survivors in one cluster (10/0)

Ten survivors in a 34 m x 50 m patch around (155, 355), 12-16 m from their nearest neighbour (so the 5 m merge never joins two real survivors). Numbered in order of confirmation.

| # | Clock | Survivor (x, y) | Lane | ETA DEL-01 | ETA DEL-02 | Assigned | Free kits after (01 / 02) |
|---:|---|---|---:|---|---|---|---|
| 1 | 8:01 | S1 (140, 330) | 7 | 34.7 s (pad) | 34.7 s (pad) | DEL-01 (tie -> lower ID) | 4 / 5 |
| 2 | 8:02 | S2 (152, 338) | 7 | 26.4 s | 35.7 s | DEL-01 | 3 / 5 |
| 3 | 8:03 | S3 (146, 348) | 7 | 51.2 s | 36.6 s | DEL-02 | 3 / 4 |
| 4 | 8:05 | S4 (138, 362) | 7 | 52.8 s | 26.6 s | DEL-02 | 3 / 3 |
| 5 | 8:06 | S5 (155, 368) | 7 | 53.0 s | 51.8 s | DEL-02 | 3 / 2 |
| 6 | 8:07 | S6 (150, 380) | 7 | 54.2 s | 76.3 s | DEL-01 | 2 / 2 |
| - | 8:44 | S5 seen again | 8 | - | - | Merged (no kit) | 2 / 2 |
| 7 | 8:45 | S7 (168, 365) | 8 | 77.3 s | 76.3 s | DEL-02 | 2 / 1 |
| 8 | 8:46 | S8 (160, 352) | 8 | 78.0 s | 101.5 s | DEL-01 | 1 / 1 |
| 9 | 8:47 | S9 (172, 345) | 8 | 101.4 s | 102.0 s | DEL-01 | 0 / 1 |
| 10 | 8:49 | S10 (165, 332) | 8 | Not a candidate | 103.3 s | DEL-02 | 0 / 0 |

| Drone | Order of drops (clock) | Time spent holding for clearance |
|---|---|---|
| DEL-01 (18 m) | S1 9:21, S2 10:13, S6 11:08, S8 11:37, S9 12:04 | 52 s in total |
| DEL-02 (22 m) | S3 9:47, S4 10:14, S5 10:42, S7 11:09, S10 11:37 | 16 s in total |

- Both drones work the same cluster. The 20 m rule makes them take turns when drop points are close; simultaneous drops happen only when the two drop points are more than 20 m apart (for example S2 and S4, 27.8 m apart).
- Holds are also caused by INS-01 passing nearby on lanes 8 and 9; holds last seconds, not minutes.
- All 10 kits delivered by 12:04; all drones landed by about 13:10 in the model.

### 4.3 Example 3: a delivery drone runs low on battery

Same survivors as Example 1. At 5:00 DEL-02 reports low battery and returns (ASSUMED event for the example).

| Clock | Event | GCS action | Free kits (01 / 02) |
|---|---|---|---|
| 5:00 | DEL-02 low battery, RTL; it was flying to S6 | DEL-02 removed from candidates; S6 released from its queue | 1 / - |
| 5:00 | S6 re-allocated | Only candidate DEL-01: ETA 67.0 s (42.0 + 25 x 1) -> DEL-01; kit reserved | 0 / - |
| 6:41, 9:15, 10:25 | S8, S9, S10 confirmed | No candidate with a free kit -> UNSERVED, shown red on the map with geotag | 0 / - |
| 7:03 | DEL-01 delivers S6 | Ledger COUNTED | 0 / - |

- Delivered: S1-S7 (7 kits). DEL-02 lands with 3 kits still on board; they leave the pool.
- S8-S10 are still detected and geotagged and reported to the GCS, so detection and geotag credit is not lost.
- Rule: delivered = kits already delivered + free kits on the healthy drone. The allocator never "borrows" a kit from a drone that is returning for safety.

### 4.4 Duplicate sightings

- Adjacent lanes overlap by about 7.4 m (29.6 m optical footprint, 22.2 m lane spacing), so a survivor near a lane edge can be seen twice.
- INS-01 merges confirmations within 5 m; the GCS applies the same 5 m rule as the final authority.
- A merged sighting only refines the stored position (running mean). It never creates a new survivor, never reserves a kit.
- Both worked examples above contain such a duplicate (S3 in Example 1, S5 in Example 2).

---

## 5. Airspace deconfliction

```text
 22 m  ----------------- DEL-02 transit layer -------------------------------
 18 m  ----------------- DEL-01 transit layer -------------------------------
 12 m  ----------------- INS-01 search layer (lawnmower, 8 m/s) -------------
  3 m  ..... drop height: only after descent clearance ..... [box falls 0.78 s]
  0 m  ==== ground ====================================== [pad: one movement at a time]

                 drop point
            <---- 20 m ---->o<---- 20 m ---->
       descent allowed only if INS-01 and the other DEL are outside this circle
```

| Rule | Value | Protects against |
|---|---|---|
| Vertical layers | INS-01 12 m, DEL-01 18 m, DEL-02 22 m | Crossing paths in transit and search (6 m and 4 m layer gaps) |
| Descent clearance | INS-01 and other DEL >= 20 m horizontal from the drop point | A DEL descending through the 12 m layer near INS-01, or two DELs dropping side by side |
| Drop height | 3 m (TFmini-S) | Short fall, low drift |
| Pad interlock | One take-off or landing at a time; each drone has its own pad spot | Rotor wash and collisions at the pad |
| DEL wait state | Disarmed on the pad until first assignment | Battery saving; empty airspace during early search |
| Geofence | Built from the KML, uploaded to all three | Leaving the zone |

Clearance granting (being coded):

- The GCS grants descents in request order. A drone that must wait holds at its transit altitude outside the other drop point's 20 m circle, so two waiting drones cannot block each other.
- Proposed addition: apply the same 20 m check before the climb back through the 12 m layer. INS-01 at 8 m/s crosses a 40 m circle in about 5 s, so the wait is short.

---

## 6. Failure handling in the collaboration layer

| Event | How the GCS knows | GCS action | Effect on the mission |
|---|---|---|---|
| DEL link lost | Heartbeat timeout | Not a candidate; undelivered queue reassigned; drone does failsafe RTL | No release possible without a GCS command |
| DEL low battery / failsafe RTL | Battery or failsafe flag | Same as above; its kits leave the pool | Served = delivered + free kits on the other DEL |
| Release not confirmed (no beam) | Lua beam report missing | Kit UNKNOWN; never re-fired automatically; survivor flagged | Avoids a double kit on one survivor |
| INS-01 lost or AI stopped | Telemetry / Pi watchdog | No new survivors; lanes flown without AI shown as unsearched | DELs still serve every survivor already confirmed |
| No free kit anywhere | Allocator | Survivor UNSERVED, still geotagged | Detection and geotag still reported |
| EMERGENCY RECALL | Operator button | All drones abort and RTL, staggered landing; pending release cancelled | Safety over score |

---

## 7. Time efficiency from parallel operation

Example 1 timeline (each character = 15 s; L = launch, S = searching, R = return and land, # = DEL airborne, . = on pad):

```text
clock (min)  0   1   2   3   4   5   6   7   8   9   10  11  12  13
             |---|---|---|---|---|---|---|---|---|---|---|---|---|---
INS-01       LLSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSRRRRRR.....
DEL-01       ...#######################...........#######..........
DEL-02       ......####################.########.......##########..
```

| Item | Value | Type |
|---|---|---|
| Search of 10 ha (9 lanes, 4,678 m path) | 10.15 min; ends 10:39 including 30 s launch | CALCULATED |
| Last kit (Example 1) | 11:51 | CALCULATED (timeline model) |
| All drones landed (Example 1 / 2) | about 13:03 / 13:10 | CALCULATED (timeline model) |
| Team planning figure | about 14-15 min (conservative 14.7 min) | CALCULATED |
| Margin to the 30-min cap | about 15 min | CALCULATED |
| Fallback search at 10 m AGL | 12.34 min search (+2.2 min) | CALCULATED |
| Endurance on paper (mission profile) | INS-01 46.8 min, DEL loaded 37.6 min | CALCULATED on an unverified thrust curve |

- Two delivery drones need about 10 x 40 s = 6.7 drone-minutes for 10 kits, while detections arrive over about 10 minutes, so delivery keeps pace with the search.
- The only delivery that must wait for the end of the search is the last survivor found.

---

## Evidence status

| Item | Status | Note |
|---|---|---|
| Roles and data flow (INS-01 / DEL-01 / DEL-02 / GCS) | DESIGNED | Final 3-drone architecture |
| ETA allocation rule, candidates, tie-break, pooled kits | BUILT-SIMULATED (core logic) | Allocation logic exists in our GCS software and passes our software simulation on an earlier fleet configuration; 3-drone adaptation being coded now |
| Kit ledger and duplicate merge (5 m) | BUILT-SIMULATED (core logic) | Same status as above |
| Worked examples 1-3 and their timelines | CALCULATED | Timeline model on ASSUMED speeds and times |
| 10 m/s transit, 25 s service, 20 s launch, 30 s landing | ASSUMED | To be replaced by flight-log values |
| Altitude layers, 20 m descent clearance, pad interlock | DESIGNED | Being coded in the 3-drone GCS |
| In-request-order clearance and climb-out check | DESIGNED (proposed) | To be coded and tested |
| Reassignment on DEL failure / low battery | DESIGNED | Being coded |
| ArduPilot SITL run of the 3-drone mission | PENDING TEST | Next step |
| Any multi-drone flight | PENDING TEST | Nothing is flight-tested |
| Mission time 14-15 min | CALCULATED | Pending a timed rehearsal |

---

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| GCS or its radio is a single point of failure | No new allocations or releases | Each drone keeps ArduPilot failsafes (link-loss RTL, low battery, geofence); release only on GCS command; every decision logged |
| INS-01 is the only searcher | No new survivors if it fails | Automatic pre-flight checks (GNSS, gimbal, camera, AI running); Pi watchdog; DELs still serve every confirmed survivor |
| Greedy ETA ignores the remaining transit time of a drone's current leg | Occasionally not the fastest choice | Acceptable for 10 survivors over 10 min (queues stay at 1-2); option to add remaining leg time later; decisions logged for review |
| Two DELs waiting for each other in a cluster | Deadlock, lost time | Clearances granted in request order; the waiting drone holds outside the 20 m circle |
| Climb-out through INS-01's 12 m layer | Close approach | Same 20 m check before climb (proposed); vertical layers; geofence |
| Several take-offs and landings per DEL | More pad movements, more risk | Pad interlock; drones re-tasked in the air when possible |
| Two real survivors closer than 5 m | Merged into one: one kit and one detection lost | Keep two boxes seen in the same frame as two survivors (proposed); merge threshold tuned in tests |
| Telemetry bandwidth with three drones on RFD868x | Late commands or reports | Low-rate telemetry; survivor reports are small; link health shown per drone; WPC and frequency check |

---

## Likely judge questions

1. Why not let the delivery drones decide among themselves?
   They have no companion computer, and the GCS already sees all three drones. One decision-maker avoids double claims of a survivor or a kit. The cost is dependence on the GCS link, which is covered by link-loss RTL and the rule that kits are released only on a GCS command.

2. Is the nearest-ETA rule optimal?
   No. It is a simple greedy rule chosen because it is predictable and easy to verify. Survivors arrive one at a time over about 10 minutes and queues stay short, so in our timeline model every kit is delivered on average 75 s after confirmation. Measured times from SITL and flight logs will tell us whether a better rule is worth the complexity.

3. What happens if all 10 survivors are in one corner?
   Both DEL drones go there. The 20 m descent rule makes them take turns when drop points are close. In our 10/0 model all 10 kits are delivered within about 4 minutes of the first confirmation (8:01 to 12:04), with 52 s and 16 s of total waiting.

4. Have you flown three drones together?
   No. The allocation, kit ledger and release logic exist in our GCS software and pass our software simulation; the 3-drone version is being coded now. SITL and flight tests are pending.
