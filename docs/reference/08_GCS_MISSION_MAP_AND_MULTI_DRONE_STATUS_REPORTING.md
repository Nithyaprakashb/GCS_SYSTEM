# GCS Mission Map and Multi-Drone Status Reporting — 20 marks

**NIDAR 2026 · Problem Statement 1 · Phase 4A design review · Rubric row 8**
**Fleet shown on the GCS:** INS-01 (inspection quad), DEL-01 and DEL-02 (delivery hexas)
**Status in one line:** the GCS core is built and has run end to end in simulation on an earlier fleet configuration. The three-drone version, with screens for each drone's role, is being coded now. It has not yet run on ArduPilot SITL or on real aircraft.

## Key message

One offline GCS shows all three drones at once: one mission map, a status card for each drone's role, one survivor list, a timeline of every automatic decision and three live video feeds side by side. The operator can only start the mission or recall the fleet.

---

## Slide plan

### Slide 1 — "One offline GCS for the whole fleet"
- A Windows desktop app (.exe, Electron) with a Python server, running with no internet.
- MAVLink 2 runs over an RFD868x network of 3 air radios and 1 ground radio, with one system ID per drone.
- Each drone's analog 5.8 GHz video goes to its own receiver and USB capture device, and all three feeds reach the one GCS.
- Four screens: Mission, Tactical map, Live video and Log, each with a dark theme and a light theme for sunlight.
- The operator has START MISSION (enabled only when all checks pass) and EMERGENCY RECALL (two clicks within 4 s).
- *Suggested visual:* the data-path diagram from Section 1 next to a Mission-screen screenshot.

### Slide 2 — "Mission map: what was searched, found and delivered"
- The KML boundary is drawn on the map and the same polygon is uploaded as the geofence. INS-01's lanes and live camera footprint are shown on it.
- A probability-of-detection heatmap in 5 m cells, using 1 - (1 - p)^passes, plus a list of coverage gaps.
- A live mosaic and the trails of all three drones, with no internet map tiles needed.
- Survivor markers are coloured by status and labelled with the drone that owns them (for example "S4 · DEL-01").
- Each DEL drone's route runs to its current target and then through its queued targets, with 1 / 2 / 3 m drop rings around each survivor.
- *Suggested visual:* an annotated map screenshot with one callout per layer.

### Slide 3 — "Multi-drone status, live video and proof"
- INS-01 card: search progress, AI detector health, detections, battery, altitude, speed, GNSS, RSSI and ETA.
- DEL cards: kits left x/5, queue, current target and ETA, task state, battery, height above ground (AGL), and break-beam and gate health.
- Top bar: mission phase, mission clock against 30 min with a 15-min marker, a link light per drone, a SIMULATION / REAL badge and the manual-input counter at 0.
- Three live video panels, each with a HUD and automatic recording, and a VIDEO OFFLINE indicator when a feed drops.
- Every mission saves telemetry, events, plan, survivors, score, operator actions and video.
- *Suggested visual:* a mock-up of the three status cards above the three video panels, plus the mission-folder listing.

---

## 1. System architecture and data path

```text
 AIRCRAFT                                              GCS LAPTOP (offline)
 ---------------------------------------------------   -------------------------------------------
 INS-01  Cube Orange+ <-serial- Pi 5 + Hailo-8
         (survivor reports)     RFD868x air --+
 DEL-01  Cube Orange+ + Lua script            |  MAVLink 2       RFD868x ground radio (USB)
         (beam, gate, TFmini-S) RFD868x air --+-- 868 MHz ---->    -> GCS server (Python)
 DEL-02  Cube Orange+ + Lua script            |  3 air + 1 ground     fleet state, planner, uploads,
         (beam, gate, TFmini-S) RFD868x air --+                       survivor list, allocator,
                                                                      delivery state machines,
                                                                      kit ledger, checks,
                                                                      fail-safe monitor, recorder

 INS-01  RunCam Nano 2 -> TX800 --5.8 GHz ch A--> receiver A -> USB capture --+
 DEL-01  RunCam Nano 2 -> TX800 --5.8 GHz ch B--> receiver B -> USB capture --+--> video service
 DEL-02  RunCam Nano 2 -> TX800 --5.8 GHz ch C--> receiver C -> USB capture --+    (3 panels, HUD,
                                                                                    recording)
                                    GCS server + video service
                                               | whole fleet state pushed at 5 Hz
                                               v
                      Electron app (React + MapLibre GL, no internet tiles)
                      Mission | Tactical map | Live video | Log
                      Operator: [ START MISSION ]   [ EMERGENCY RECALL ]
```

| Data | Path | Rate |
|---|---|---|
| Heartbeat, mode, position, attitude, battery, GNSS, radio RSSI | ArduPilot telemetry streams from each drone | A few Hz per drone, tuned so all three fit the shared radio network |
| INS-01 survivor reports (ID, position, confidence, geotag error, time) | Pi 5 -> Cube serial -> RFD868x, MAVLink | When a survivor is confirmed |
| DEL rangefinder height, gate and beam state | ArduPilot rangefinder data and messages from the Lua script | A few Hz, plus a message on each release |
| Commands: mission and fence upload, GUIDED targets, kit release, RTL, LAND | GCS -> drone, MAVLink, acknowledged | When needed |
| Screen update | GCS server -> app | 5 Hz |
| Mission record | `telemetry.csv` | 2 Hz |
| Live video | Analog 5.8 GHz, one channel per drone | Continuous |

The same software runs in **SIMULATION** and **REAL** mode, and a badge in the top bar always shows which one is active. The simulation never fills in for real hardware that is missing.

## 2. Screens

| Screen | Purpose | Main content |
|---|---|---|
| **Mission** | Run the whole mission from one screen | 3 status cards, compact map, 3 live video panels, survivor list, autonomy timeline, top bar |
| **Tactical map** | Check coverage and positions in detail | Full-size map with all layers and layer switches, plus an analysis panel: area searched, mean probability of detection, predicted finish, each drone's distance to the boundary, gaps, warnings |
| **Live video** | Watch every feed large | Three panels side by side, each with its HUD and recording state |
| **Log** | Audit and review | Full event history with search, pre-flight results and operator actions |

There is a dark theme and a light theme for outdoor sunlight.

## 3. Mission map

The map is built with MapLibre GL. It draws everything from the organiser KML and live telemetry, so it needs **no internet and no map tiles**. The live mosaic adds an aerial picture of the field.

| Layer | What it shows | Why it matters |
|---|---|---|
| Boundary / geofence | KML polygon (dashed). The same polygon is uploaded as the autopilot fence on all three drones | Shows that the fence matches the organiser area |
| INS-01 search lanes | 9 planned lanes (12 m AGL, 8 m/s) with ends 3 m inside the boundary; flown lanes highlighted | Shows full planned coverage without touching the fence |
| INS-01 camera footprint | The ground area the AI camera sees now (about 29.6 m x 15.9 m at 12 m) | Links detections to a place on the map |
| Probability-of-detection heatmap | 5 m cells (about 4,000 for 10 ha), each coloured by 1 - (1 - p)^passes. Lanes flown while the AI was down count as not searched | Shows search quality, not just where the drone flew |
| Coverage gaps | The largest unsearched areas, with size and position | Shows what is still missing |
| Live mosaic | Aerial picture built from captured video frames and telemetry | A picture of the field without internet |
| Drone markers and trails | Position, heading, altitude and task label of all three drones, each in its own colour, with the path flown | Live picture of the whole fleet |
| Survivor markers | Numbered markers coloured by status (CONFIRMED, ASSIGNED, DELIVERED, UNKNOWN, UNSERVED). Each label shows the owner drone, for example "S4 · DEL-01", and a circle shows the geotag error | One shared list with no duplicates |
| DEL routes and queues | A line from each DEL drone to its current target, then through its queued targets in order | Shows who is going where next |
| Drop rings | 1 m, 2 m and 3 m rings around each survivor, with the actual release point | Shows delivery accuracy honestly |
| Pad | The 12 ft pad with each drone's spot | Shows the launch and landing order |
| Controls | An on/off switch for each layer and a "fit boundary" button | Keeps the map readable |

```text
 +------------------------------------------------------------------------------+
 |  - - - - - - - - - - KML boundary = geofence - - - - - - - - - -  [layers]   |
 | |  lane 1  ==========================================================>    |  |
 | |  lane 2  <==========================================================    |  |
 | |  lane 3  ==================[INS-01]=> . . . . . . . . . . . . . . .    |  |
 | |                           [ camera footprint ]                         |  |
 | |  lane 4  . . . . . . . . . . planned . . . . . . . . . . . . . . . .   |  |
 | |      (S1 DELIVERED · DEL-01)            (S3 ASSIGNED · DEL-02)         |  |
 | |        o 1/2/3 m drop rings     DEL-02 route --> S3 --> S5 (queued)    |  |
 | |  heatmap: dark = high probability of detection, grey = not searched    |  |
 | |  [PAD: INS-01 | DEL-01 | DEL-02]                                       |  |
 |  - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -   |
 +------------------------------------------------------------------------------+
   Sketch for the slide only; the real map is drawn from the KML and telemetry.
```

## 4. Multi-drone status reporting

### 4.1 Top bar (always visible)

| Item | Content |
|---|---|
| Mission phase | Setup, pre-flight, search, delivery, return or complete |
| Mission clock | Elapsed time on a 0–30 min bar, with a marker at 15 min |
| Link lights | One per drone: green (healthy), amber (degraded), red (lost) |
| Mode badge | SIMULATION or REAL |
| Manual-input counter | Number of penalised operator inputs (target: 0) |

### 4.2 INS-01 status card

| Field | Content |
|---|---|
| Identity and task | INS-01 in its own colour; task: Standby, Launching, Searching (lane n of 9), Returning or Landed |
| Search progress | Lanes done / 9, % of area covered, mean probability of detection |
| AI detector health | Pi heartbeat, detector running, frames per second, Pi temperature, gimbal level |
| Detections | Candidates seen, survivors confirmed |
| Flight | Mode, armed or disarmed, altitude, speed, heading |
| Battery | Voltage, %, estimated minutes left |
| GNSS | Fix type, satellites, HDOP |
| Radio | RSSI at the GCS and at the drone |
| ETA | Predicted end of the search |
| Fail-safe | The active fail-safe, if any |

### 4.3 DEL-01 / DEL-02 status cards

| Field | Content |
|---|---|
| Identity and task | DEL-01 or DEL-02 in its own colour; task: Standby, Launching, Delivering, Dropping or Returning |
| Kits | Kits left x/5, split into loaded / reserved / delivered / unknown |
| Queue | Survivor IDs in order |
| Current target | Survivor ID, distance, ETA |
| Flight | Mode, armed or disarmed, altitude, height above ground from the TFmini-S, speed |
| Battery | Voltage, %, minutes left, and whether there is enough energy for the queue plus RTL |
| Delivery hardware | Break-beam OK, gate servo OK, result of the last release |
| Link and GNSS | RSSI, satellites, HDOP |
| Fail-safe | The active fail-safe, if any |

### 4.4 Survivor list (one shared list for the fleet)

| Column | Content |
|---|---|
| ID | S1 … S10 |
| Status | CONFIRMED, ASSIGNED, DELIVERED, UNKNOWN or UNSERVED |
| Coordinates | Latitude and longitude (mean of 3 geotags) |
| Geotag error | ± m, from the spread of the 3 geotags |
| Confidence | Detector confidence |
| Owner | DEL-01 or DEL-02 |
| Drop result | Break-beam confirmed or UNKNOWN, and the release point's distance from the survivor |

### 4.5 Autonomy timeline

Every automatic decision is logged with its time and shown on the Mission screen. The lines below show the layout; the times are made up.

```text
00:00:00  START MISSION (allowed input; penalised inputs = 0)
00:00:30  INS-01 on lane 1 (AUTO)
00:03:12  S1 confirmed by INS-01 (3 frames, mean of 3 geotags, error +/- x.x m)
00:03:12  S1 -> DEL-01 (ETA DEL-01 xx s, DEL-02 xx s; tie -> lower ID)
00:03:14  DEL-01 launch granted (pad clear)
00:04:05  DEL-01 holding above S1: INS-01 within 20 m, waiting
00:04:09  DEL-01 descending to 3 m
00:04:24  DEL-01 release sent; beam confirmed; S1 DELIVERED; DEL-01 kits 4/5
```

### 4.6 Automatic warnings

- A drone within 2 m of the boundary, or outside it
- Any pair of drones closer than 8 m
- A DEL drone whose battery cannot cover its queue plus RTL with reserve
- Predicted finish later than 30 min, or past the 15-min marker
- Telemetry link degraded or lost (per drone)
- Video offline (per panel)
- AI detector down on INS-01
- A release with no break-beam signal (marked UNKNOWN and never re-fired)
- A flight-mode change the GCS did not command (for example an RC takeover)

### 4.7 Mission screen layout

```text
+-----------------------------------------------------------------------------------+
| SEARCH | 07:42 / 30:00 [=======|----15----|-----------]  links: INS o DEL1 o DEL2 o |
| SIMULATION | Manual inputs: 0                                                      |
+--------------------+--------------------+--------------------+-------------------+
| INS-01  Searching  | DEL-01  Delivering | DEL-02  Standby    | SURVIVORS         |
| lane 6/9   xx %    | kits 3/5  queue S5 | kits 5/5  queue -  | S1 DELIVERED DEL-01 |
| AI OK   xx fps     | target S4  ETA xx s| on pad, disarmed   | S2 DELIVERED DEL-01 |
| batt xx %  sats xx | AGL xx m  beam OK  | batt xx %  beam OK | S4 ASSIGNED  DEL-01 |
+--------------------+--------------------+--------------------+-------------------+
|          MAP: boundary, lanes, heatmap, drones, survivors, routes, drop rings     |
+---------------------------+---------------------------+---------------------------+
| VIDEO INS-01  (HUD, REC)  | VIDEO DEL-01  (HUD, REC)  | VIDEO DEL-02  (HUD, REC)  |
+---------------------------+---------------------------+---------------------------+
| AUTONOMY TIMELINE ...                                     [ EMERGENCY RECALL ]     |
+-----------------------------------------------------------------------------------+
  Layout mock-up; xx values are placeholders, not results.
```

## 5. Live video from every drone

| Item | Design |
|---|---|
| Panels | Three panels, INS-01, DEL-01 and DEL-02, all visible at the same time on one GCS (the rule asks for live video from every drone) |
| Video path | RunCam Nano 2 -> SpeedyBee TX800 (analog 5.8 GHz, one channel per drone) -> ground receiver -> USB capture device -> GCS video service |
| HUD overlay | Drawn by the GCS from telemetry: drone ID, mode, altitude / AGL, speed, heading, battery, task. INS-01 also shows its detection count; DEL drones show kits left |
| Recording | Every feed is recorded automatically from START, with timestamps that line up with the telemetry |
| Loss of signal | The panel shows **VIDEO OFFLINE**. Flight does not depend on video |
| Simulation | Synthetic camera view with a **SIMULATION** watermark |
| INS-01 note | The live feed comes from the analog camera. The AI runs on the separate Pi Camera 3 Wide, and its detections reach the GCS by telemetry and appear on the map |
| Channel plan | Three channels spread well apart inside the band that WPC and the venue allow, chosen after a spectrum check. For example 5740 / 5800 / 5860 MHz (60 MHz apart), to be confirmed. The VTX uses the lowest power that gives a clean picture |
| Panel-to-drone binding | Each capture device is tied to a fixed USB port. The pre-flight check confirms that each feed appears under the correct drone ID |

**Ground video hardware (not counted in airborne mass):** 3 x 5.8 GHz receivers, 3 x USB analog capture devices, a powered USB hub (for the 3 capture devices and the RFD868x ground radio), 3 receive antennas with polarisation matched to the drones, and receiver power from the GCS field power pack.

## 6. Pre-flight checks and operator controls

### 6.1 Automatic pre-flight checks (per role)

| Check | INS-01 | DEL-01 / DEL-02 |
|---|---|---|
| Telemetry link (heartbeat, RSSI) | Yes | Yes |
| GNSS fix | 3D fix, 8 or more satellites | 3D fix |
| Battery | Yes | Yes |
| Geofence uploaded, matches the KML, enabled | Yes | Yes |
| Mission / parameters uploaded and acknowledged | Search mission | Delivery and fail-safe parameters |
| Pi 5 and AI detector running | Yes | — |
| 5 kits loaded and break-beam OK | — | Yes |
| Rangefinder (TFmini-S) valid | — | Yes |
| Gate servo tested (before the kits are loaded) | — | Yes |
| On its pad spot, inside the boundary | Yes | Yes |
| Disarmed | Yes | Yes |

That is 8 checks for INS-01 and 10 for each DEL drone, 28 drone checks in all. The GCS also checks at fleet level that all three video panels show the correct drone and that the plan estimate is under 30 min. No check can be passed by clicking it.

### 6.2 Operator controls

| Control | When it is enabled | Effect |
|---|---|---|
| Load mission (KML) | Setup | Builds the plan, the fence and the uploads |
| **START MISSION** | Only when every check on all three drones is green | INS-01 launches; DEL drones launch on their first assignment |
| **EMERGENCY RECALL** | After START; needs two clicks within 4 s | All drones RTL. The command is sent repeatedly to each drone until its heartbeat shows RTL, and any pending kit release is cancelled |
| ABORT (land in place) | In the same protected emergency group | The selected drone or drones land where they are |
| Everything else | Locked | — |

Every operator action is logged with its time in `operator_actions.json` and counted in the top bar.

## 7. Mission records

Each mission saves its own folder automatically:

| File | Content |
|---|---|
| `telemetry.csv` | All three drones at 2 Hz |
| `events.jsonl` | Every event and automatic decision (detections, assignments, reassignments, drops, mode changes, fail-safes) |
| `plan.json` | Boundary, lanes, fence, time estimate |
| `survivors.json` | Final survivor list with owner and drop result |
| `score.json` | Score estimate and how it was worked out, labelled as an estimate |
| `operator_actions.json` | Every button press, for the audit |
| `recordings/` | All three video feeds |

## 8. How the map and requirements line up

| Requirement | Where it is met on the GCS |
|---|---|
| Single GCS for all drones | One app, one fleet state for INS-01, DEL-01 and DEL-02 |
| Live video from every drone at the same time | Three panels on the Mission and Live video screens |
| Mission map | KML boundary, lanes, footprint, heatmap, gaps, mosaic, survivors, routes, drop rings |
| Multi-drone status reporting | Top bar, a status card for each role, survivor list, timeline, warnings |
| Offline operation | No tiles, no cloud; everything is drawn from the KML and telemetry |
| Minimal manual input | START and the emergency controls only; counter and audit file |
| Geofence awareness | Boundary = fence; a 2 m warning; each drone's distance to the boundary in the analysis panel |

## 9. Build status

| Part | Status |
|---|---|
| Electron app + Python server, offline, MAVLink 2, SIMULATION / REAL modes | BUILT-SIMULATED (earlier fleet configuration) |
| Map layers: boundary and fence, lanes with the 3 m inset, footprint, heatmap, gaps, mosaic, trails, survivor markers, drop rings | BUILT-SIMULATED |
| Survivor list with the 5 m merge, autonomy timeline, warnings, top bar with the manual-input counter | BUILT-SIMULATED |
| Automatic pre-flight gate, START / EMERGENCY RECALL lock, audit log, mission records | BUILT-SIMULATED |
| Mission and geofence upload over MAVLink with acknowledgement | BUILT-SIMULATED |
| Video panels with HUD, recording, offline indicator and SIMULATION watermark | BUILT-SIMULATED (synthetic camera) |
| Three-drone fleet model: INS card and DEL cards, DEL routes and queues, kit-ledger view, per-role checks | DESIGNED, being coded |
| Three analog feeds through USB capture devices | DESIGNED, being coded |
| Three ArduPilot SITL instances against the GCS | PENDING TEST |
| Real aircraft over the RFD868x network, real analog video | PENDING TEST |

## 10. 60-second demo script (simulation, clearly labelled)

1. **Load the KML.** The boundary, the 9 INS-01 lanes and the time estimate appear, and the fence upload is acknowledged by all three drones.
2. Show the per-role checks turning green. START MISSION unlocks.
3. **Press START.** INS-01 launches, while DEL-01 and DEL-02 stay on the pad and their cards show Standby.
4. Show the heatmap filling in and the gap list shrinking.
5. When a survivor is confirmed, show its marker, error circle and owner label, the ETA decision in the timeline, the DEL launch and route line, the drop rings and the beam-confirmed DELIVERED.
6. Point at the manual-input counter (0) and at EMERGENCY RECALL, the only control left.

---

## Evidence status

| Item | Status |
|---|---|
| GCS core (map, heatmap, survivor list, timeline, warnings, START / RECALL lock, logs, MAVLink upload) | BUILT-SIMULATED (earlier fleet configuration) |
| Three-drone role-aware cards, DEL routes and queues, kit-ledger view | DESIGNED (being coded) |
| Three live analog panels via USB capture, HUD, recording | DESIGNED (being coded); panels and recording exist with a synthetic camera |
| Heatmap cell count (about 4,000 cells of 5 m for 10 ha) and camera footprint (29.6 m x 15.9 m at 12 m) | CALCULATED |
| Single-pass detection probability p used in the heatmap | ASSUMED until it is measured on dummies |
| Radio stream rates for three drones on one RFD868x network | PENDING TEST |
| Video latency and range of the 5.8 GHz links | PENDING TEST |
| Three-drone SITL; real aircraft | PENDING TEST |

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| Three air radios share one ground radio | Slower updates, more latency | Configure the RFD868x network for 3 + 1 nodes; tune stream rates in SITL and on the bench; show a link light per drone; link-loss RTL as the backstop |
| Three identical USB capture devices come up in a random order | A feed appears under the wrong drone name | Tie each panel to a fixed USB port; a pre-flight check confirms each drone's feed |
| 5.8 GHz interference or use not permitted | Snowy or lost video | WPC and venue approval; channels well apart; lowest power that works. Flight does not depend on video, and VIDEO OFFLINE is shown |
| The GCS laptop crashes or loses power | No display and no new commands | Every airborne drone does link-loss RTL; logs are written during the mission; field power pack |
| Live mosaic quality depends on the analog camera's angle | A poor picture | The mosaic is a visual aid only. Detection, geotags and scoring do not use it |
| Too much information on one screen | Operator misses an alert | Separate cards for each role, layer switches, a full-size Tactical map, warnings in the timeline |
| Simulation mistaken for real data | False confidence | SIMULATION badge and watermark; the simulation never fills in for missing hardware |
| Sunlight on the laptop screen | Screen hard to read | Light theme; shade over the GCS table |

## Likely judge questions

**Q1. Is the telemetry on screen real?**
In the demo, simulated drones speak real MAVLink and the screen says SIMULATION. The same code connects to the aircraft through the RFD868x network, but the three-drone SITL and real-aircraft tests are still pending.

**Q2. How do you show three live videos at once with no internet?**
Each drone has its own analog 5.8 GHz camera link on its own channel. Three receivers feed three USB capture devices on the GCS laptop, and each panel adds a HUD drawn from telemetry and records automatically.

**Q3. How does the operator know the area was really searched?**
The probability-of-detection heatmap colours every 5 m cell by the chance a person there would have been found. Lanes flown while the AI was down count as unsearched, and the gap list shows what is missing.

**Q4. Why don't INS-01's AI detections appear on its live video?**
The AI uses the Pi Camera 3 Wide, while the live feed comes from a separate analog camera. Detections reach the GCS by telemetry and appear on the map, the survivor list and the INS-01 card.
