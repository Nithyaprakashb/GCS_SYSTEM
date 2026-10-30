# Mission Architecture and System Design — 20 marks

Team Adrods_X | NIDAR 2026-27 | RescueSwarm | Problem Statement 1 | Phase 4A design review

All performance numbers in this file are calculated or assumed. None of them has been checked yet on a thrust stand or in flight.

## Key message

One AI search quadcopter finds and geotags survivors. Two simpler delivery hexacopters, dispatched by a single offline GCS, drop kits from a shared pool in parallel. The result is a calculated mission time of about 14–15 minutes with no manual input after START.

## Slide plan

### Slide 1 — System architecture

- Three aircraft and one GCS: INS-01 search quad, DEL-01 and DEL-02 delivery hexas, and one offline GCS laptop.
- Telemetry and commands: a 4-node RFD868x network (865–867 MHz) carrying MAVLink 2.
- Video: three analog 5.8 GHz channels on separate frequencies, feeding three USB capture panels on the GCS.
- Only INS-01 carries AI (Pi 5 + Hailo-8 26 TOPS running YOLOv8n). The delivery drones have no companion computer; the GCS commands them.
- The operator can only load the mission, press START MISSION, or press EMERGENCY RECALL.
- Suggested visual: the telemetry and video diagrams in section 1, redrawn as block diagrams.

### Slide 2 — Mission flow and timeline

- Setup (5 minutes or less): import the KML, run automatic pre-flight checks, generate the lane plan.
- Search: 9 lanes x 500 m at 12 m AGL and 8 m/s, which takes 10.2 min.
- Detect, confirm (3 frames at 85% or more), geotag, then the GCS merges duplicates within 5 m.
- Assign each survivor to the delivery drone with the smallest ETA and a free kit. Transit is at 18 m (DEL-01) or 22 m (DEL-02). A drone descends to 3 m only when the other drones are at least 20 m away.
- Release, confirm with the break-beam, go to the next target or return. Landings on the 12 ft pad are staggered. Calculated total is about 14–15 min.
- Suggested visual: swimlane timeline with rows for INS-01, DEL-01, DEL-02 and GCS (section 4.3).

### Slide 3 — Key design decisions

- A dedicated search drone carries the only AI. That means one dataset and one camera calibration, and no compute mass or cost on the delivery drones.
- Kits are pooled across DEL-01 and DEL-02, so any distribution of survivors can be served.
- Altitude layers (12 / 18 / 22 m) and a 20 m descent rule keep the drones apart without on-board collision sensing.
- All three drones use the same motors, propellers, battery and autopilot: one thrust-test campaign and shared spares.
- Fleet mass is 14.814 kg against 25 kg. Hover endurance is 53.3 min (INS-01) and 43.1 min (DEL), against a mission of about 15 min (CALCULATED).
- Suggested visual: decision table plus a mass and endurance bar chart.

---

## 1. System diagram

### 1.1 Telemetry and command network (MAVLink 2 over RFD868x)

```
                 +---------------------------------------------------+
                 |        GROUND CONTROL STATION (single laptop)     |
                 |  Electron UI: React + MapLibre (no internet tiles)|
                 |  Python server: KML import, lane planner,         |
                 |  pre-flight checks, survivor list, 5 m merge,     |
                 |  kit allocation, separation rules, auto logging   |
                 |  Buttons: START MISSION | EMERGENCY RECALL        |
                 +-------------------------+-------------------------+
                                           | USB serial
                                 +---------+---------+
                                 | RFD868x ground    |
                                 | modem (node 0)    |
                                 +---------+---------+
                                           :  865-867 MHz, MAVLink 2
              +----------------------------+----------------------------+
              :                            :                            :
   +----------+----------+      +----------+----------+      +----------+----------+
   | INS-01  (node 1)    |      | DEL-01  (node 2)    |      | DEL-02  (node 3)    |
   | 650 mm X4 search    |      | 900 mm X6 delivery  |      | 900 mm X6 delivery  |
   | Cube Orange+, M10   |      | Cube Orange+, M10   |      | Cube Orange+, M10   |
   | TELEM2 <-> Pi 5     |      | TFmini-S (AGL)      |      | TFmini-S (AGL)      |
   |   + Hailo-8 AI HAT+ |      | 5-kit magazine      |      | 5-kit magazine      |
   | Cam 3 Wide on       |      | 2x MG90S latches    |      | 2x MG90S latches    |
   |   2-axis gimbal     |      | IR break-beam (Lua) |      | IR break-beam (Lua) |
   +---------------------+      +---------------------+      +---------------------+
```

### 1.2 Live video network (separate from telemetry)

```
 INS-01: RunCam Nano 2 -> SpeedyBee TX800 (ch A) ~~5.8 GHz~~> RX A -> USB capture 1 -> GCS panel 1
 DEL-01: RunCam Nano 2 -> SpeedyBee TX800 (ch B) ~~5.8 GHz~~> RX B -> USB capture 2 -> GCS panel 2
 DEL-02: RunCam Nano 2 -> SpeedyBee TX800 (ch C) ~~5.8 GHz~~> RX C -> USB capture 3 -> GCS panel 3
```

### 1.3 On-board detection chain (INS-01 only)

```
 Camera 3 Wide (nadir, held level by gimbal)
   -> Pi 5: frame resized to 1280 px wide, split into 2 tiles of 640 x 640
   -> Hailo-8: YOLOv8n person detection
   -> confirm: same target in 3 frames, confidence >= 85%
   -> geotag: pixel offset x height / focal length, rotated by heading, added to GNSS position
   -> MAVLink 2 survivor report -> Cube TELEM2 -> Cube TELEM1 -> RFD868x -> GCS
   -> GCS: merge duplicates within 5 m, add to survivor list, assign a delivery drone
```

The Pi 5 to Cube serial link (TELEM2) is part of the design but is not yet in the workbook wiring table. It will be added before the G1 bench gate.

## 2. Roles

| Asset | Role | Key hardware | Flight profile | Payload | Autonomy |
|---|---|---|---|---|---|
| INS-01 | Search the whole 10 ha alone; detect, confirm and geotag survivors | 650 mm X4, 4x Tarot TL2955 330KV, 15-in Tarot 1555 folding props, Cube Orange+, Pi 5 8 GB + Hailo-8 AI HAT+ 26 TOPS, Camera 3 Wide (12 MP, 102° HFOV) on a 2-axis gimbal, M10 GNSS, RFD868x, RunCam Nano 2 + TX800 | 12 m AGL, 8 m/s, 9 lanes, 3 s turns | Camera and AI only | Fully autonomous lane mission; detections reported automatically |
| DEL-01 | Deliver kits to assigned survivors | 900 mm flat X6, 6x TL2955 330KV, 15-in props, Cube Orange+, M10, RFD868x, TFmini-S, 5-box magazine, 2x MG90S latch servos, IR break-beam, RunCam Nano 2 + TX800 | Waits on the pad until its first assignment; transit at 18 m AGL, about 10 m/s; drop from 3 m AGL | 5 kits (1 kg) | GCS-commanded targets; release sequence run by a Lua script on the Cube |
| DEL-02 | Deliver kits to assigned survivors | Same as DEL-01 | Same as DEL-01, but transit at 22 m AGL | 5 kits (1 kg) | Same as DEL-01 |
| GCS | Plan, monitor, allocate, log | Offline laptop, Electron + Python, RFD868x ground modem, 3 video receivers and USB capture devices | Ground | — | Automatic checks, allocation and logging; operator has two buttons |

## 3. Data, command and video flows

### 3.1 Flow table

| Flow | From -> To | Link | Content | Timing (DESIGNED) |
|---|---|---|---|---|
| Mission upload | GCS -> INS-01 | RFD868x, MAVLink 2 | Lane waypoints clipped to the KML polygon, geofence, home | During setup |
| Geofence and parameters | GCS -> all three | RFD868x | KML boundary plus buffer, RTL altitude per drone (12 / 18 / 22 m) | During setup |
| Health telemetry | Each drone -> GCS | RFD868x | Heartbeat, position, altitude, battery, mode, mission state | About 1–4 Hz per drone (to be tuned for a 3-drone network) |
| Survivor report | INS-01 Pi 5 -> Cube -> GCS | Serial (TELEM2), then RFD868x | ID, latitude, longitude, confidence, time | Event-based, after 3-frame confirmation |
| Delivery command | GCS -> DEL-01 / DEL-02 | RFD868x | Target position, transit altitude, permission to descend, release command | Event-based |
| Release confirmation | DEL Cube Lua -> GCS | RFD868x | Kit number released, break-beam result (OK / FAIL) | Event-based |
| Recall | GCS -> all three | RFD868x | Return-to-launch with staggered landing | On EMERGENCY RECALL or end of mission |
| Live video | Each drone -> GCS | 5.8 GHz analog, one channel per drone | Camera image | Continuous, independent of telemetry |
| Logs | All | On board and GCS | Cube dataflash logs, Pi detection log, GCS mission log | Automatic |

### 3.2 Telemetry network design

- Four RFD868x nodes in one multipoint network: GCS (node 0), INS-01 (node 1), DEL-01 (node 2), DEL-02 (node 3). Each aircraft has a unique MAVLink system ID.
- With three aircraft sharing one radio channel, stream rates are kept low and commands have priority. Throughput and latency with three aircraft are PENDING TEST.
- Radio use at 865–867 MHz at the configured power needs a device-specific WPC approval check. The frequency label alone does not prove approval.

### 3.3 Video channel plan

- One SpeedyBee TX800 per drone, each on its own channel. Channels are chosen as far apart as possible inside the band allowed at the venue. One example is 5740 / 5800 / 5860 MHz, which are 60 MHz apart. Final channels and power will follow WPC rules and organiser instructions.
- Transmit power is set to the lowest level that gives clean video across the field. The worst-case distance is about 550 m (the corner of a 500 x 200 m zone).
- Three capture devices on one laptop may exceed USB bandwidth. We will use MJPEG-capable capture devices or separate USB controllers and test all three panels at the same time.

## 4. Mission phase flow

### 4.1 Phase diagram

```
[SETUP <= 5 min] -> [START] -> [SEARCH] -> [DETECT + GEOTAG] -> [ASSIGN] -> [DELIVER] -> [RTL + LAND]
 load KML           1 click     INS-01      3 frames >= 85%     min ETA     3 m AGL      staggered
 auto checks                    9 lanes     pinhole geotag      free kit    break-beam   12 ft pad
 lane plan                      12 m, 8 m/s 5 m merge (GCS)     pooled kits confirm
                                   ^                                           |
                                   +--- search continues while deliveries run -+
```

### 4.2 Phase details

| Phase | Trigger | INS-01 | DEL-01 / DEL-02 | GCS | Human |
|---|---|---|---|---|---|
| 1. Setup | 5-min window starts | Powered, pre-arm checks | Powered, magazines loaded, pre-arm checks | Imports KML, plans lanes clipped to the polygon, uploads missions and geofences, runs automatic pre-flight checks (GPS, battery, links, video, kit count) | Load mission |
| 2. Start | START MISSION pressed | Takes off, climbs to 12 m, starts lane 1 (about 0.5 min) | Wait on the pad, disarmed or idle | Starts mission clock and log | Press START |
| 3. Search | Lane mission running | 9 lanes x 500 m at 8 m/s with 3 s turns: 10.2 min | Waiting or delivering | Shows track, coverage and video | None |
| 4. Detect and geotag | Person seen in 3 frames at 85% or more | Computes geotag and sends a survivor report | — | Merges reports within 5 m, adds to the survivor list | None |
| 5. Assign | New survivor in list | — | — | Picks the DEL with the smallest ETA that still has a free kit | None |
| 6. Deliver | Assignment received | — | Take off (if on pad), transit at its own layer, wait until the others are at least 20 m away, descend to 3 m (TFmini-S), hover and verify, release, break-beam confirms, climb back | Gives descent permission, records each release | None |
| 7. Return and land | Search done and all kits assigned, or no kits left | Returns at 12 m | Return at 18 m / 22 m | Lands the drones one at a time on assigned pad spots, saves logs | None (EMERGENCY RECALL only if needed) |

### 4.3 Timeline estimate (CALCULATED)

| Time from START | INS-01 | DEL-01 / DEL-02 | Note |
|---|---|---|---|
| T+0 to T+0.5 min | Take-off, climb, start of first lane | On pad | |
| T+0.5 to T+10.7 min | Search: 10.2 min | Deliveries start as soon as the first survivor is confirmed; each one takes about 15–30 s of transit at 10 m/s plus about 25 s of service | Deliveries run in parallel with the search |
| about T+11.7 to T+13 min | Return | Last drop, for a survivor found near the end of the search | Depends on where the last survivors are |
| +1.5 to 2 min | Return and staggered landing of all three drones on the 12 ft pad | | |
| Total | | | About 14–15 min. That leaves about 15 min of margin under the 30-min cap. The bonus is possible but not claimed until measured |

The 25 s service time is built up from ArduPilot default climb and descent speeds (ASSUMED): descending 15–19 m at 1.5 m/s takes about 10–13 s, hovering to verify and releasing takes about 4 s, and climbing back at 2.5 m/s takes about 6–8 s.

## 5. Search design

### 5.1 Altitude and speed choice

Camera: Camera Module 3 Wide, 102° horizontal field of view. The frame is used at 1280 px wide. Lane spacing is 80% of the optical swath (ASSUMED overlap). The zone is modelled as 500 x 200 m.

| Option | Optical swath | Lane spacing (80%) | Lanes | Search time | Pixels across a 0.5 m target |
|---|---|---|---|---|---|
| **12 m AGL, 8 m/s (chosen)** | 29.64 m | 23.71 m | 9 x 500 m | **10.2 min** | 21.6 px |
| 10 m AGL, 8 m/s | 24.70 m | 19.76 m | 11 x 500 m | 12.4 min | 25.9 px |
| 7 m AGL, 5 m/s | 17.29 m | 13.83 m | 15 x 500 m | 26.3 min | 37.0 px |

- Swath = 2 x height x tan(51°). Search time = (lane length + crossover length) / speed + 3 s per turn.
- Ground sample distance at 12 m is about 2.3 cm per pixel (29.64 m / 1280 px). A 0.5 m person (shoulder width, the smallest dimension) is about 21.6 px.
- Motion blur at a 1/1000 s shutter and 8 m/s is 0.8 cm, which is under half a pixel.
- Each ground point stays in view for about 2 s at 8 m/s (along-track footprint of about 16 m with the 67° vertical field of view), so 3-frame confirmation needs at least about 1.5 processed frames per second. The real Hailo-8 frame rate is PENDING TEST.
- The real KML polygon may not be a rectangle. The planner clips lanes to the polygon, so the real search time is recalculated during setup.

### 5.2 Detection and confirmation

- Model: YOLOv8n (small), fine-tuned on our own survivor dataset, run on Hailo-8.
- Input: two 640 x 640 tiles per 1280 px frame, so the image is not shrunk and small targets keep their pixels.
- Confirmation: the same target in 3 frames with confidence of 85% or more before it is reported. This filters single-frame false alarms.
- Camera settings (DESIGNED): fixed focus near infinity, so autofocus does not hunt at 12 m, and a short shutter of about 1/1000 s.

### 5.3 Geotag model (simple nadir pinhole)

The gimbal keeps the camera level and pointing straight down, so a flat-ground pinhole model is enough:

```
 f (pixels)      = (1280 / 2) / tan(51°)  = 518.3 px
 forward offset  = -(v - cy) x H / f        (H = height above ground)
 right offset    =  (u - cx) x H / f
 north  = forward x cos(heading) - right x sin(heading)
 east   = forward x sin(heading) + right x cos(heading)
 survivor lat/lon = drone GNSS lat/lon + (north, east) converted to degrees
```

Geotag error budget. The inputs are ASSUMED and the result is CALCULATED. Field measurement is PENDING TEST.

| Error source | Assumed input | Ground error |
|---|---|---|
| Standalone GNSS position (M10, no RTK) | about 2 m | about 2.0 m |
| Height error | 0.5 m | up to 0.62 m at the image edge (14.8 m offset) |
| Heading error | 2° | up to 0.52 m at the image edge |
| Gimbal level error | 1° | 0.21 m |
| Timing (frame to position) | 50 ms at 8 m/s | 0.40 m |
| Root-sum-square | | about 2.2 m |

The 5 m duplicate-merge radius is about twice this estimate. GNSS is expected to be the largest error.

## 6. Allocation and delivery logic

### 6.1 Allocation rule

```
on new confirmed survivor S (after the 5 m merge):
    candidates = delivery drones that are healthy and have a free (unreserved) kit
    for each candidate D:
        ETA(D) = time for D to finish its current queue
               + distance(end of D's queue, S) / 10 m/s
               + 25 s service time
    assign S to the candidate with the smallest ETA; reserve one kit on it
```

- Kits are pooled. No zone is fixed to a drone, so any distribution of survivors works: 5 and 5, 7 in one half and 3 in the other, or all 10 in one half. Each delivery drone carries 5 kits, so all 10 kits can always be used.
- A delivery drone waits on the pad until it gets its first assignment. This saves energy and keeps the airspace clear.
- If a release fails (no break-beam signal), the drone retries once. If that also fails, the GCS marks the kit as not delivered and re-assigns the survivor to the other drone if it has a free kit. This is DESIGNED and PENDING TEST.

### 6.2 Delivery sequence for one target

1. Take off (if on the pad) and climb to the drone's own transit layer: 18 m for DEL-01, 22 m for DEL-02.
2. Fly to the target position at about 10 m/s.
3. Hold until no other drone is within 20 m horizontally (checked by the GCS).
4. Descend to 3 m AGL using the TFmini-S downward lidar for height.
5. Hover and verify the position and height for a few seconds.
6. The GCS sends the release command. The Lua script on the Cube runs the 2-stage escapement and reads the IR break-beam.
7. Report the release result, climb back to the transit layer, then fly to the next target or return.

### 6.3 Deconfliction

| Rule | Value | Purpose |
|---|---|---|
| Altitude layers | INS-01 12 m, DEL-01 18 m, DEL-02 22 m | Vertical separation in cruise and on return |
| Descent rule | Descend to 3 m only when the other drones are at least 20 m away horizontally | No drone passes through another drone's layer near a third drone |
| Return | Each drone returns at its own layer | No crossing at the same height |
| Landing | Staggered, one drone below 10 m over the pad at a time, each on an assigned spot | Pad shared by three drones |

The current GCS runs in simulation. The 3-drone deconfliction rules are being implemented in the adapted version and will be tested in simulation before any field test.

### 6.4 Pad layout (12 ft = 3.66 m square; DESIGNED and CALCULATED)

| Aircraft | Spot centre (x, y) from one pad corner | Tip-to-tip size |
|---|---|---|
| DEL-01 | (0.92 m, 0.92 m) | about 1.28 m |
| DEL-02 | (2.74 m, 0.92 m) | about 1.28 m |
| INS-01 | (1.83 m, 2.74 m) | about 1.03 m |

Propeller-tip gaps are about 0.54 m between the two hexas and about 0.88 m between a hexa and the quad. The smallest gap to the pad edge is about 0.28 m. Standalone GNSS landing scatter could be 1–2 m (ASSUMED), so landings are staggered and the scatter will be measured. If it is larger than about 0.5 m, a precision-landing aid will be considered.

## 7. Design decisions and why

| Decision | Why | Trade-off we accept |
|---|---|---|
| A dedicated search drone carries the only AI (Pi 5 + Hailo-8 + camera + gimbal) | One dataset, one calibration and one compute stack. The AI hardware (₹31,748 for Pi 5 + AI HAT+) is bought once. The delivery drones carry no compute mass or power | INS-01 is a single point of failure for detection |
| Delivery drones are simple and commanded by the GCS | Fewer parts, lower mass, no companion computer; the release logic runs on the Cube | Delivery depends on the telemetry link for new targets |
| Kits are pooled across two delivery drones | Works for any survivor distribution; nearest drone serves first | The allocator must handle reservations and re-assignment |
| Search at 12 m AGL and 8 m/s | 10.2 min search with 21.6 px on a 0.5 m target and 0.8 cm motion blur | Small targets for YOLOv8n. Fallback is 10 m AGL (25.9 px, 12.4 min) if recall is too low |
| 3-frame confirmation at 85% or more, 5 m merge | Fewer false alarms and duplicate deliveries | Two survivors closer than 5 m could be merged into one; to be checked in testing |
| Altitude layers and a 20 m descent rule | Deconfliction without extra sensors or mass | Small waiting time before some descents |
| Analog 5.8 GHz video, one channel per drone | Low latency, no on-board processing, independent of telemetry | Three receivers and capture devices at the GCS; regulatory check needed |
| Same motor, propeller, battery and autopilot on all drones | One thrust-test campaign, shared spares and charging | The quad carries the same 2,350 g battery as the hexas |
| Drop from 3 m with lidar height | Free fall of 0.782 s; drift is 0.39–1.56 m at 0.5–2 m/s residual speed | Kit packaging must survive a 3 m fall |

## 8. Interfaces

| Interface | Between | Physical | Protocol / signal | Status |
|---|---|---|---|---|
| Battery to power module | 6S 22 Ah -> PM07 | XT90-S anti-spark, 10 AWG | 22.2 V nominal | DESIGNED |
| Power module to autopilot | PM07 -> Cube Orange+ POWER1 | Power harness | Voltage and current sense, 5 V supply | PENDING TEST (pinout must be checked before power-up) |
| Motor control | Cube MAIN 1–4 (INS) or 1–6 (DEL) -> 40 A ESCs | 3-pin servo leads | PWM | DESIGNED |
| Telemetry | Cube TELEM1 <-> RFD868x | UART; 5 V from a separate RF regulator | MAVLink 2 | DESIGNED |
| Companion link (INS-01) | Pi 5 UART <-> Cube TELEM2 | UART | MAVLink 2 | DESIGNED (to be added to the wiring table) |
| AI accelerator | Pi 5 <-> Hailo-8 AI HAT+ | PCIe FPC | PCIe | DESIGNED |
| Camera | Pi 5 <-> Camera 3 Wide | 22-pin CSI FFC | CSI-2 | DESIGNED |
| Gimbal (INS-01) | Cube MAIN 5 -> gimbal controller | 3-pin lead | PWM tilt (nadir) | DESIGNED |
| GNSS | Cube GPS port <-> M10 | UART cable (not DroneCAN) | UBX / NMEA | DESIGNED |
| Height lidar (DEL) | Cube GPS2 / TELEM3 <-> TFmini-S | 4-pin JST-GH | UART | DESIGNED |
| Escapement servos (DEL) | Cube MAIN 7 and 8 -> 2x MG90S | 3-pin leads; power from a separate servo BEC | PWM | DESIGNED |
| Break-beam (DEL) | IR break-beam -> Cube AUX pin used as GPIO | 3-wire | Digital input read by Lua (check logic voltage) | DESIGNED |
| FPV video | RunCam Nano 2 -> TX800 -> ground receiver | Coax and antenna | Analog 5.8 GHz | DESIGNED |
| Video into GCS | Receiver -> USB capture -> laptop | USB | UVC video | DESIGNED |
| Ground telemetry | RFD868x ground modem -> laptop | USB serial | MAVLink 2 | BUILT-SIMULATED (GCS side) |
| Mission input | KML file -> GCS | USB stick | KML polygon | BUILT-SIMULATED (GCS side) |
| Operator | Human -> GCS | Screen and mouse | START MISSION, EMERGENCY RECALL | BUILT-SIMULATED |

## 9. Mass and power summary

| Parameter | INS-01 | DEL-01 / DEL-02 (each) | Status |
|---|---|---|---|
| All-up mass | 3.987 kg | 5.414 kg loaded / 4.414 kg empty | CALCULATED |
| Payload | Camera, gimbal and AI | 5 kits = 1.0 kg (18.5% of take-off mass) | DESIGNED |
| Hover thrust per motor | 996.7 g (45% of 2,200 g) | 902.3 g (41% of 2,200 g) | CALCULATED |
| Thrust-to-weight at the 100% point | 2.21 | 2.44 | CALCULATED (unverified curve) |
| Propulsion power at hover | 354.5 W | 463.6 W | CALCULATED |
| Avionics (+10% regulator loss) | 30.6 W | 12.4 W | CALCULATED |
| Total hover power and current | 385.2 W, 17.4 A | 476.0 W, 21.4 A | CALCULATED |
| Hover endurance (341.88 Wh = 70% of 488.4 Wh) | 53.3 min | 43.1 min | CALCULATED + ASSUMED |
| Mission-profile endurance (1.15x propulsion) | 46.8 min | 37.6 min | CALCULATED + ASSUMED |
| Flight time needed in the mission | about 12–13 min | up to about 14 min | CALCULATED |

Fleet take-off mass is 3.987 + 2 x 5.414 = 14.814 kg against the 25 kg limit, a margin of 10.186 kg. This is not yet weighed.

## 10. Safety and failsafes

| Event | Automatic response | Status |
|---|---|---|
| Telemetry lost to one drone | ArduPilot failsafe: return at that drone's own layer altitude; no kit is released without a GCS command | DESIGNED, PENDING TEST |
| Low battery | Return to launch | DESIGNED, PENDING TEST |
| Geofence breach (KML polygon plus buffer) | Return to launch | DESIGNED, PENDING TEST |
| EMERGENCY RECALL pressed | All three return at their layers and land one at a time | BUILT-SIMULATED (button); 3-drone sequence in adaptation |
| Release not confirmed by the break-beam | Retry once, then report and re-assign | DESIGNED, PENDING TEST |
| Another aircraft close to a descending delivery drone | Descent permission only when the other drones are at least 20 m away | DESIGNED, PENDING TEST |
| Video link lost | Mission continues; telemetry is independent of video | DESIGNED |

## Evidence status

| Item | Status | Basis |
|---|---|---|
| Three-drone architecture, roles, flows | DESIGNED | This review |
| GCS software (Electron, Python, MAVLink 2, offline map, buttons, logging) | BUILT-SIMULATED | Existing GCS runs in simulation; 3-drone version being adapted |
| Allocation (smallest ETA with a free kit), pooled kits, deconfliction rules | DESIGNED | Being implemented in the 3-drone GCS |
| Search time 10.2 min; swath 29.64 m; 21.6 px on target | CALCULATED | Camera geometry and lane model |
| 80% lane overlap, 3 s turns, 10 m/s transit, 25 s service | ASSUMED | Planning values |
| Mission time about 14–15 min | CALCULATED | Timeline model |
| Geotag error of about 2.2 m | CALCULATED from ASSUMED inputs | Error budget; field test pending |
| Detection recall and frame rate on Hailo-8 | PENDING TEST | Dataset and benchmark |
| 4-node RFD868x throughput, 3-channel video, USB capture | PENDING TEST | Link tests |
| Mass and power figures | CALCULATED | BOM masses and unverified reference thrust curve |

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| INS-01 fails or is grounded | No detections, so no deliveries | Automatic pre-flight checks; endurance margin (46.8 min against about 12–13 min needed); spare parts; recall if it fails in flight |
| YOLOv8n misses small targets (21.6 px) | Survivors not found | Fine-tune on our own dataset collected at 12 m; tiles at full resolution; fallback to 10 m AGL (25.9 px, 12.4 min) |
| Two real survivors closer than 5 m merged into one | One survivor gets no kit | Tune the merge radius from geotag tests; do not merge targets seen as separate boxes in the same frame (planned refinement) |
| 4-node telemetry congestion | Late commands or reports | Low stream rates, command priority, measured latency in G5 |
| USB bandwidth with three capture devices | Video panels freeze | MJPEG capture devices or separate USB controllers; test all three at once |
| Landing scatter on a shared 12 ft pad | Contact between aircraft | Assigned spots, staggered landing, scatter measurement, precision-landing aid if needed |
| Kit lands on or near a person | Injury risk (about 7.7 m/s impact from 3 m, before drag) | Check the rulebook drop radius; a small offset from the geotag is under review; padded kit |
| Radio approval (865–867 MHz, 5.8 GHz) | Links not permitted | Device-specific WPC check; organiser confirmation of channels and power |

## Likely judge questions

**Q1. What happens if a delivery drone loses the telemetry link mid-delivery?**
It runs the ArduPilot link-loss failsafe and returns at its own layer altitude. A kit is never released without a GCS command. The survivor stays in the GCS list and can be re-assigned to the other delivery drone. This is designed but not yet tested.

**Q2. How do you prevent collisions between three drones?**
Each drone flies at its own altitude layer: 12 m for INS-01, 18 m for DEL-01 and 22 m for DEL-02. The GCS allows a descent to 3 m only when the other drones are at least 20 m away, and landings are staggered on assigned pad spots. There is no on-board collision sensor. These rules are being added to the 3-drone GCS and will be tested in simulation first.

**Q3. Why put the AI on only one drone?**
One search drone covers the full 10 ha in a calculated 10.2 minutes, so the delivery drones only need coordinates. Keeping the AI on one drone means one dataset, one calibration and one compute stack, and it keeps the delivery drones lighter and simpler. The cost is that INS-01 is a single point of failure for detection, which we cover with pre-flight checks and spares.

**Q4. Is 14–15 minutes realistic?**
It is a calculation, not a measurement: 0.5 min climb, 10.2 min search, the last drop about 1–2 min later, then 1.5–2 min to land. It depends on assumed speeds, 3 s turns and 25 s per drop. We will measure it in the full-mission rehearsal. Until then we do not claim the bonus. The 30-minute cap leaves about 15 minutes of margin.
