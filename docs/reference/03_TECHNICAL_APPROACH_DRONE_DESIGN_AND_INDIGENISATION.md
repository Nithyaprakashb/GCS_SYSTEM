# Technical Approach, Drone Design and Indigenisation — 20 marks

Team Adrods_X | NIDAR 2026-27 | RescueSwarm | Problem Statement 1 | Phase 4A design review

All performance numbers in this file are calculated or assumed. None of them has been checked yet on a thrust stand or in flight. The thrust data is an unverified reference curve (workbook sheet 7).

## Key message

All three drones share one propulsion set (Tarot TL2955 330KV with 15-inch props), one autopilot (Cube Orange+) and one battery (6S 22 Ah). On paper this gives thrust-to-weight of 2.21 (INS-01) and 2.44 (DEL) and 43–53 minutes of hover against a mission of about 15 minutes. Every one of these numbers will be checked by a thrust stand, a weigh-in and a 50-release bench test before flight.

## Slide plan

### Slide 1 — Airframes and propulsion

- INS-01 is a 650 mm X4 with 4x TL2955 330KV motors and 15-in folding props. AUW is 3.987 kg, hover needs 996.7 g per motor (45% of the 2,200 g reference maximum), and T/W is 2.21.
- DEL-01 and DEL-02 are 900 mm flat X6 frames with 6x TL2955. AUW is 5.414 kg loaded and 4.414 kg empty, hover needs 902.3 g per motor (41%), and T/W is 2.44. Adjacent propeller tips are about 69 mm apart.
- Hover power is 385.2 W (INS-01) and 476.0 W (DEL). Hover endurance is 53.3 and 43.1 min at 70% usable energy (CALCULATED).
- The DEL is 8.3% above Tarot's guidance of about 5 kg for a hexa, so a thrust-stand temperature check comes before flight.
- Suggested visual: CAD top views of both frames with dimensions, and the reference thrust curve with both hover points marked.

### Slide 2 — Power, payload and avionics

- Power path: 6S 22 Ah (488.4 Wh) -> XT90-S and 10 AWG -> PM07 -> 40 A ESCs. Separate 5 V UBEC for the Pi, RF regulator, video regulator and servo BEC.
- Hover current is 17.4 A (INS-01) and 21.4 A (DEL). Full-thrust peaks are about 47 A and 71 A, so the ~30 A stock PM07 lead is replaced with rated 10 AWG wiring and an XT90-S connector.
- Payload: a 5-kit gravity magazine with a 2-stage load-bearing escapement. The MG90S servos only move latches; they never carry the stack.
- An IR break-beam read by a Lua script on the Cube confirms each release. A TFmini-S lidar holds the 3 m drop height.
- Suggested visual: power block diagram, and a magazine cross-section showing the 4-step release sequence.

### Slide 3 — Manufacturing, indigenisation, cost and tests

- Made in-house: airframe CAD and CNC carbon plates, 6061 motor mounts, PETG gimbal plate, magazine and escapement, GCS, allocation logic, geotag pipeline, Lua script, and the survivor dataset with model fine-tuning.
- Core electronics are imported and bought through Indian distributors. We state this plainly and show a roadmap to more local parts.
- Cost is ₹546,113 for 3 drones, the GCS, 10 kits and FPV sets. Ground video receivers and capture devices are still to be priced.
- Test plan: weigh-in, thrust stand, power bench, 50-release bench test, tethered hover, then field tests.
- Suggested visual: indigenisation table (in-house / Indian-sourced / imported) and a cost-by-subsystem bar chart.

---

## 1. Design targets

These are the team's own design targets (ASSUMED good practice), apart from the 25 kg limit, which is a rule.

| Target | Value | INS-01 (calculated) | DEL (calculated) |
|---|---|---|---|
| Thrust-to-weight at full throttle | 2.0 or more | 2.21 | 2.44 (loaded) / 2.99 (empty) |
| Hover thrust as a share of maximum | 50% or less | 45% | 41% |
| Hover endurance against mission flight time | at least 2x | 53.3 min against about 12–13 min | 43.1 min against up to about 14 min |
| Fleet take-off mass | 25 kg or less (rule) | 14.814 kg fleet total | |
| Drop height | 3 m, measured by lidar | — | TFmini-S, range 0.1–12 m |
| Release confirmation | Electronic, every kit | — | IR break-beam read by Lua |

## 2. Airframe design

### 2.1 Geometry and propeller clearance

| Parameter | INS-01 | DEL-01 / DEL-02 |
|---|---|---|
| Layout | X4 quad | Flat X6 hexa |
| Wheelbase (motor to opposite motor) | 650 mm | 900 mm |
| Arm radius (centre to motor) | 325 mm | 450 mm |
| Spacing between adjacent motors | 459.6 mm (650 / √2) | 450 mm (equal to the radius at 60° spacing) |
| Propeller | Tarot 1555 folding, 15 x 5.5 in (381 mm) | Same |
| Static gap between adjacent propeller tips | about 79 mm | about 69 mm |
| Tip-to-tip span | about 1,031 mm | about 1,281 mm |
| Central zone outside the propeller discs (radius) | about 135 mm | about 260 mm |
| Disc loading at hover | 85.8 N/m² | 77.6 N/m² |

- The DEL frame is 900 mm because an 800 mm hexa with 15-inch props leaves only about 19 mm between adjacent tips. At 900 mm the gap is about 69 mm. Arm flex and the gap with motors running still need to be checked.
- No one-motor-out capability is claimed for the hexa. It has not been analysed or tested.
- INS-01 camera: with a 102° horizontal field of view pointing straight down, the landing legs may appear in the image. The CAD will place the legs outside the field-of-view cone, or the image region will be masked.

### 2.2 Mass budget

INS-01 (BOM masses; nothing weighed yet)

| Item | Mass (g) | Qty | Total (g) |
|---|---|---|---|
| 650 mm carbon X4 frame (original estimate) | 580 | 1 | 580 |
| Tarot TL2955 4008 330KV motor | 85 | 4 | 340 |
| Tarot 1555 folding propeller (pair) | 35 | 2 | 70 |
| 40 A 2–6S ESC | 35 | 4 | 140 |
| Cube Orange+ Standard Set | 75 | 1 | 75 |
| Raspberry Pi 5 8 GB | 45 | 1 | 45 |
| AI HAT+ (Hailo-8, 26 TOPS) | 25 | 1 | 25 |
| Camera Module 3 Wide | 14 | 1 | 14 |
| 2-axis brushless gimbal | 160 | 1 | 160 |
| RFD868x | 50 | 1 | 50 |
| Holybro M10 GNSS | 32 | 1 | 32 |
| Holybro PM07 | 35 | 1 | 35 |
| Matek 5 V / 5 A UBEC | 15 | 1 | 15 |
| Herewin 6S 22000 mAh 25C | 2,350 | 1 | 2,350 |
| BOM subtotal | | | 3,931 |
| FPV video set (RunCam 3.2 + TX800 5.6 + 5 V regulator 12 + antenna, coax and bracket 35) | | | 55.8 |
| **AUW** | | | **3,986.8 g = 3.987 kg** |

DEL-01 / DEL-02 (each)

| Item | Mass (g) | Qty | Total (g) |
|---|---|---|---|
| 900 mm flat X6 frame (placeholder carried over from an 800 mm estimate) | 920 | 1 | 920 |
| Tarot TL2955 330KV motor | 85 | 6 | 510 |
| Tarot 1555 propeller (pair) | 35 | 3 | 105 |
| 40 A ESC | 35 | 6 | 210 |
| Cube Orange+ Standard Set | 75 | 1 | 75 |
| Benewake TFmini-S | 15 | 1 | 15 |
| TowerPro MG90S | 14 | 2 | 28 |
| RFD868x | 50 | 1 | 50 |
| Holybro M10 GNSS | 32 | 1 | 32 |
| Holybro PM07 | 35 | 1 | 35 |
| Herewin 6S 22000 mAh 25C | 2,350 | 1 | 2,350 |
| Medical kits (200 g, 20 x 10 x 5 cm) | 200 | 5 | 1,000 |
| BOM subtotal | | | 5,330 |
| FPV video set | | | 55.8 |
| IR break-beam pair | | | 8 |
| Servo BEC | | | 20 |
| **AUW loaded / after 5 drops** | | | **5,413.8 g = 5.414 kg / 4.414 kg** |

- Payload fraction is 1.0 / 5.414 = 18.5%. The DEL is 8.3% above Tarot's hexa guidance of about 5 kg (414 g over).
- Not yet in either mass list: magazine and escapement structure, landing gear (priced but not massed), wiring harness, RF regulator, and the 25 g PETG gimbal plate. The real AUW will be higher. Each extra 100 g on a DEL adds 16.7 g of hover thrust per motor.
- Sensitivity (CALCULATED): with 10% more mass, INS-01 is 4.386 kg (T/W 2.01), DEL is 5.955 kg (T/W 2.22), and the fleet is 16.30 kg, still well under 25 kg.

## 3. Propulsion, thrust, power and endurance

### 3.1 Reference thrust curve (unverified; 15-in prop at 24 V)

| Point | Current per motor | Power per motor | Thrust per motor | Efficiency |
|---|---|---|---|---|
| 50% throttle | 2.7 A | 64.8 W | 790 g | 12.19 g/W |
| 70% (interpolated in workbook) | 5.96 A | 142.9 W | 1,366 g | 9.56 g/W |
| 75% throttle | 6.77 A | 162.5 W | 1,510 g | 9.29 g/W |
| 100% throttle | 11.77 A | 282.5 W | 2,200 g | 7.79 g/W |

### 3.2 Hover point

| Parameter | INS-01 | DEL loaded | DEL empty |
|---|---|---|---|
| Motors | 4 | 6 | 6 |
| AUW | 3.987 kg | 5.414 kg | 4.414 kg |
| Hover thrust per motor | 996.7 g | 902.3 g | 735.6 g |
| Share of 2,200 g reference maximum | 45% | 41% | 33% |
| T/W at the 100% point | 2.21 | 2.44 | 2.99 |
| Interpolated efficiency at hover | 11.25 g/W | 11.68 g/W | — |
| Hover power per motor | 88.6 W | 77.3 W | — |
| Propulsion power at hover | 354.5 W | 463.6 W | — |

Thrust share is not the same as throttle position. Throttle against thrust must be measured; it cannot be worked out from a square-root rule.

### 3.3 Power and endurance

| Parameter | INS-01 | DEL |
|---|---|---|
| Propulsion (hover) | 354.5 W | 463.6 W |
| Avionics, including 10% regulator loss | 30.6 W | 12.4 W |
| Total hover power | 385.2 W | 476.0 W |
| Battery current at 22.2 V | 17.4 A (0.79 C) | 21.4 A (0.97 C) |
| Usable energy, ASSUMED 70% of 488.4 Wh | 341.88 Wh | 341.88 Wh |
| **Hover endurance** | **53.3 min** | **43.1 min** |
| Mission profile (1.15x propulsion power) | 46.8 min | 37.6 min |
| Hover at 80% usable (390.72 Wh) | 60.9 min | 49.2 min |
| Sensitivity: propulsion +30%, hover | 41.7 min | 33.3 min |
| Sensitivity: propulsion +30%, mission profile | 36.6 min | 29.1 min |
| Flight time needed in the mission (CALCULATED) | about 12–13 min | up to about 14 min |

### 3.4 Cross-checks on the reference curve

- **Momentum theory.** The ideal hover power for INS-01 is about 231 W, against 354.5 W from the curve. For the DEL it is about 299 W, against 463.6 W. That means the curve implies an overall efficiency of about 0.65 (propeller, motor and ESC together). This is at the high end, so the curve may be optimistic. That is why the +30% power sensitivity is shown above. Even at +30%, every endurance figure stays at about twice the mission time or more.
- **Voltage.** The reference points were taken at 24 V. A 6S pack sits at about 22.2 V nominal and lower under load. If thrust scales roughly with voltage squared, maximum thrust at 22.2 V would be about 1,880 g. That gives T/W of about 1.89 for INS-01 and 2.09 for the DEL (CALCULATED, rough scaling). The thrust stand at real pack voltage will settle this.

## 4. Electrical power architecture

### 4.1 Block diagram

```
 Herewin 6S 22 Ah LiPo (22.2 V nominal, 488.4 Wh, 25C)
   |  XT90-S anti-spark connector, 10 AWG (replaces the ~30 A stock PM07 lead)
   v
 Holybro PM07 power module and distribution board
   |-- voltage/current sense + 5 V --> Cube Orange+ POWER1   (pinout check before power-up)
   |-- 14 AWG --> 40 A ESC x4 (INS-01) / x6 (DEL) -- 16 AWG --> TL2955 330KV motors
   |
   +-- 6S tap --> Matek 5 V / 5 A UBEC ----> Pi 5 (+ AI HAT+ Hailo-8, Camera 3 Wide)   [INS-01 only]
   +-- 6S tap --> 5 V RF regulator --------> RFD868x (data to Cube TELEM1)
   +-- 6S tap --> 5 V video regulator -----> RunCam Nano 2 + SpeedyBee TX800
   +-- 6S tap --> servo BEC (5-6 V) -------> 2x MG90S latch servos                    [DEL only]
   +-- supply per gimbal datasheet --------> 2-axis gimbal controller                 [INS-01 only]
```

### 4.2 Current check

| Parameter | INS-01 | DEL |
|---|---|---|
| Hover battery current | 17.4 A | 21.4 A |
| Hover current per motor (at 22.2 V) | about 4.0 A | about 3.5 A |
| Current per motor at the 100% point | 11.77 A | 11.77 A |
| Full-thrust total, motors only | about 47 A | about 71 A |
| ESC rating per motor | 40 A (3.4x the 100% point) | 40 A (3.4x) |
| Battery nameplate (25C x 22 Ah) | 550 A | 550 A |
| PM07 stock battery lead | about 30 A: enough for hover, not for full-thrust peaks | Same |

Decision: the battery-to-PM07 link uses 10 AWG silicone wire and an XT90-S connector, as in the workbook wiring table. The PM07 board's own current rating is checked against the 71 A peak from the Holybro datasheet, and lead temperature is checked during the thrust test.

### 4.3 Wiring table (from workbook)

| From | To | Wire | Length INS / DEL | Connector |
|---|---|---|---|---|
| 6S battery | PM07 | 10 AWG silicone | 150 / 200 mm | XT90-S anti-spark |
| PM07 | ESCs | 14 AWG silicone | 120 / 150 mm | Solder / 3.5 mm bullet |
| ESC | Motor | 16 AWG silicone | 300 / 400 mm | 3.5 mm banana |
| PM07 harness | Cube POWER1 | 22 AWG | 200 mm | Verify pinout first |
| Battery | Matek UBEC (INS-01) | 20 AWG | 100 mm | Solder / JST-RCY |
| Matek UBEC | Pi 5 (INS-01) | 18 AWG | 120 mm | USB-C or 5 V header |
| Servo BEC | Servo rail (DEL) | 22 AWG | 250 mm | Rated independent BEC |
| RF regulator | RFD868x | 22 AWG | — | Common signal ground with Cube |
| Cube | TFmini-S (DEL) | 28 AWG | 200 mm | 4-pin JST-GH (GPS2 / TELEM3) |
| Cube | MG90S x2 (DEL) | 26 AWG | 300 mm | 3-pin, MAIN OUT 7 and 8 |

Design notes:
- Separate regulators are not galvanic isolation. Grounds are routed from a single point and shared serial and PWM grounds are checked.
- RF antennas (868 MHz and 5.8 GHz) are kept away from the GNSS module and the high-current wires. A ground interference test will check this.
- The Pi 5 supply is sized from measured Pi 5 + Hailo-8 + camera peaks. The 5 V / 5 A rating is a design target, not yet proven enough.

## 5. Avionics and sensors

### 5.1 Port map (DESIGNED)

| Cube Orange+ port | INS-01 | DEL-01 / DEL-02 |
|---|---|---|
| POWER1 | PM07 | PM07 |
| MAIN 1–4 / 1–6 | ESCs 1–4 | ESCs 1–6 |
| MAIN 5 | Gimbal tilt (nadir) | — |
| MAIN 7, 8 | — | Escapement servos (upper and lower gate) |
| AUX (GPIO) | — | IR break-beam input (read by Lua) |
| TELEM1 | RFD868x | RFD868x |
| TELEM2 | Pi 5 (MAVLink 2) | — |
| GPS1 | Holybro M10 (UART, not DroneCAN) | Holybro M10 |
| GPS2 / TELEM3 | — | TFmini-S |

### 5.2 Sensor choice (workbook sensor matrix, summarised)

| Option | Can it classify survivors? | Sunlight and water | Mass and power | Our use |
|---|---|---|---|---|
| 12 MP wide camera + on-board AI | Yes, from visual features | Glare, rain and reflective water can reduce detections (to be tested) | 14 g module, plus gimbal and compute | INS-01 search sensor |
| 2D scanning lidar | No, distance points only | Small units degrade in direct sun; water absorbs or mirrors 905 nm | 190–220 g, about 3 W | Not used |
| 3D lidar | Possible, with heavy point-cloud processing | Good in sun, still poor on water | 265–800 g, 12–15 W | Not used |
| Single-point lidar (TFmini-S) | No | Height above ground only | 15 g | DEL drop height |

### 5.3 Vision chain (INS-01)

| Element | Choice | Status |
|---|---|---|
| Camera | Camera Module 3 Wide, 12 MP IMX708, 102° HFOV (120° diagonal) | DESIGNED |
| Pointing | 2-axis gimbal holds the camera level and pointing straight down | DESIGNED |
| Exposure and focus | About 1/1000 s shutter (0.8 cm blur at 8 m/s), focus fixed near infinity | DESIGNED |
| Detector | YOLOv8n, fine-tuned on our survivor dataset, 2 x 640 x 640 tiles per 1280 px frame | Dataset and fine-tuning in progress; metrics PENDING TEST |
| Accelerator | Hailo-8 (26 TOPS) on AI HAT+ | Frame rate, power and temperature PENDING TEST |
| Confirmation and geotag | 3 frames at 85% or more; nadir pinhole model, rotated by heading, added to GNSS | DESIGNED |

## 6. Payload mechanism (DEL-01 / DEL-02)

### 6.1 Concept

- The 5 kits (20 x 10 x 5 cm, 200 g each) sit in a vertical gravity-fed column about 25 cm tall, centred under the aircraft's centre of gravity. Each release lowers the mass, but the centre of gravity moves only vertically, not sideways.
- A 2-stage escapement sits at the bottom of the column. The lower gate holds the bottom kit. The upper gate is 5 cm higher and can hold the rest of the stack.
- Each gate is a sliding latch whose load passes across the latch into the magazine walls. A servo only slides the latch against friction. Its horn never carries the stack.
- The final latch geometry is in CAD. The magazine panels are composite or polymer; 1 mm carbon is not assumed to be strong enough.

### 6.2 Release sequence (one kit per cycle)

| Step | Upper gate | Lower gate | Result |
|---|---|---|---|
| At rest | Open | Closed | Stack rests on the lower gate (the load goes into the structure) |
| 1 | Closes | Closed | Upper gate takes kits 2–5 (800 g) |
| 2 | Closed | Opens | Kit 1 falls; the break-beam must see it within the timeout |
| 3 | Closed | Closes | Lower gate ready |
| 4 | Opens | Closed | Stack drops 5 cm onto the lower gate; the next kit is in position |

The lower gate must take the shock of the stack dropping 5 cm (about 1 m/s). The 50-release test checks this.

### 6.3 Servo check (MG90S, 2.2 kgf·cm stall)

| Case | Torque needed (20 mm horn) | Share of stall | Verdict |
|---|---|---|---|
| Servo holds the 1 kg stack directly, at 1 g | 2.0 kgf·cm | 91% | Not acceptable |
| Servo holds the 1 kg stack directly, at 2 g (manoeuvre or vibration) | 4.0 kgf·cm | 182% | Fails |
| Servo slides the upper latch under 800 g (friction 0.3 ASSUMED), at 1 g | 0.48 kgf·cm | 22% | Acceptable |
| Same, at 2 g | 0.96 kgf·cm | 44% | Acceptable |
| Servo slides the lower latch under one 200 g kit | 0.12 kgf·cm | 5% | Acceptable |

### 6.4 Release confirmation logic (Lua script on the Cube, DESIGNED)

```
on release command for kit n (from GCS):
    close upper gate; wait for servo travel
    open lower gate
    wait for IR beam break (timeout about 1 s)
    close lower gate; wait for servo travel
    open upper gate (stack moves down one kit)
    report "kit n OK" or "kit n FAIL" to the GCS
GCS: on FAIL -> retry once -> if it fails again, re-assign the survivor to the other DEL if it has a free kit
```

### 6.5 Drop physics (3 m AGL, CALCULATED, drag ignored)

| Quantity | Value |
|---|---|
| Fall time from 3 m | 0.782 s |
| Impact speed | about 7.7 m/s |
| Impact energy (200 g kit) | about 5.9 J |
| Drift at 0.5 m/s residual horizontal speed | 0.39 m |
| Drift at 1.0 m/s | 0.78 m |
| Drift at 2.0 m/s | 1.56 m |

- Drop height comes from the TFmini-S (range 0.1–12 m), not from GNSS altitude.
- The kit box and its packing must survive a 5.9 J impact. This will be checked in the drop test.
- Placement error is roughly the INS-01 geotag error plus the DEL GNSS error plus drift. That is about 3 m root-sum-square with the assumed inputs. It is probably less in practice, because both receivers see partly the same GNSS error at the same time and place. The real value will be measured.

## 7. Materials and manufacturing

| Part | Material | Process | Made by | Status |
|---|---|---|---|---|
| INS-01 centre plates | 3K carbon fibre, 2.0 mm | CNC milling | Bought 650 mm frame kit via ThinkRobotics (model to be confirmed); in-house CAD check of clearance and mounts | DESIGNED |
| INS-01 arms | Carbon fibre tube, 16 mm OD | Pultruded / roll-wrapped (bought) | ThinkRobotics | DESIGNED |
| DEL centre plates | Carbon fibre plate; thickness set by stress and vibration check | CNC | In-house CAD; fabricator to be fixed | DESIGNED (CAD review pending) |
| DEL arms | Carbon fibre tube (size from CAD) | Bought tube, cut in-house | In-house | DESIGNED |
| Motor mounts | 6061-T6 aluminium | CNC machining and anodising | In-house design; Tarot mounts as a bought fallback | DESIGNED |
| Gimbal plate | PETG | FDM printing, 100% infill, vibration-damped | In-house | DESIGNED (25 g) |
| Propellers | Carbon fibre | Moulded (bought) | Tarot | Bought part |
| 5-kit magazine | Composite or polymer panels | CAD, prototype, 50-release test | In-house | DESIGNED |
| Escapement latches | Load-bearing latch with Delrin guides | CAD and machining or printing | In-house | DESIGNED |
| DEL landing gear | Wide skids, size from final clearance | CAD and static landing-load test | In-house | DESIGNED (legs not cut yet) |
| Medical kit box | 20 x 10 x 5 cm, 200 g | Local packing; every box weighed and measured | In-house / local | DESIGNED |

## 8. Indigenisation

### 8.1 What is in-house now

| Item | In-house work | Status |
|---|---|---|
| Airframe design (650 mm X4, 900 mm X6) | Geometry, clearance, mounting, CAD | DESIGNED |
| Carbon centre plates (DEL frames) | CAD and CNC cutting | DESIGNED |
| 6061-T6 motor mounts | Design and CNC machining | DESIGNED |
| PETG gimbal plate | FDM printing | DESIGNED |
| 5-kit magazine and 2-stage escapement | Concept, CAD, prototype, bench test | DESIGNED |
| GCS software (Electron, React, MapLibre, Python, MAVLink 2) | Full in-house code | BUILT-SIMULATED (3-drone version in adaptation) |
| Allocation logic (smallest ETA with a free kit, pooled kits) | In-house algorithm | DESIGNED (being implemented in the 3-drone GCS) |
| Geotag pipeline (nadir pinhole, heading, GNSS) | In-house code | DESIGNED |
| Cube Lua break-beam script | In-house code | DESIGNED |
| Survivor dataset and YOLOv8n fine-tuning | In-house data collection and training (base model is open-source YOLOv8n) | In progress; metrics PENDING TEST |

### 8.2 Sourcing

| Component | Maker (imported) | Bought through (BOM link) |
|---|---|---|
| Cube Orange+ Standard Set ADS-B | CubePilot | robu.in |
| Raspberry Pi 5 8 GB, AI HAT+ (Hailo-8), Camera Module 3 Wide | Raspberry Pi / Hailo | robu.in |
| 2-axis brushless gimbal | — | electropi.in |
| RFD868x telemetry | RFDesign | mavdrones.com |
| Holybro M10 GNSS, PM07 | Holybro | robu.in |
| Herewin 6S 22 Ah LiPo | Herewin | robodo.in |
| Tarot 1555 propellers | Tarot | robu.in |
| 40 A ESC, Matek UBEC | — / Matek | robu.in |
| Benewake TFmini-S | Benewake | robu.in |
| TowerPro MG90S | TowerPro | robu.in |
| INS-01 frame | — | thinkrobotics.com |
| Tarot TL2955 330KV motors | Tarot | BOM link is store.foxtech.com (overseas); Indian distributor still to be confirmed |
| RunCam Nano 2, SpeedyBee TX800 | RunCam, SpeedyBee | Vendor still to be recorded |

### 8.3 Honest cost share

| Category | ₹ | Share of ₹546,113 |
|---|---|---|
| Imported components, bought through Indian distributors (including FPV sets) | 482,463 | 88.3% |
| Structure and payload made or assembled in-house (DEL frames, magazines, landing gear, brackets and fasteners) and the 10 kits | 25,300 | 4.6% |
| INS-01 frame (bought) | 2,450 | 0.4% |
| GCS hardware (still to be itemised) | 35,900 | 6.6% |

By cost, most of the fleet is imported electronics. Most of our own content is engineering and software, which does not show up in the BOM price.

### 8.4 Roadmap to more local content

| Step | When | Action | How we measure it |
|---|---|---|---|
| R1 | Phase 4B build | Cut the INS-01 plates in-house from our CAD with the same CNC process as the DEL; build all harnesses and brackets in-house | 100% of the structure made in-house |
| R2 | After thrust stand and first flights | Record measured data for each imported part; shortlist Indian-made battery packs, ESCs, frames and kit boxes | Candidates pass the same gates (G1–G3) |
| R3 | After the competition | Evaluate Indian-made autopilots, motors and WPC-approved telemetry; evaluate a NavIC-capable GNSS receiver | Same thrust, link and flight tests passed |
| Principle | Always | Keep ArduPilot and MAVLink 2 as open interfaces, so hardware can be swapped without changing the GCS or mission software | No software rewrite needed for a part swap |

## 9. Cost

| Category | INS-01 (₹) | DEL each (₹) | Fleet (₹) |
|---|---|---|---|
| Propulsion (motors, propellers, ESCs) | 24,546 | 36,819 | 98,184 |
| Autopilot (Cube Orange+ Standard Set ADS-B) | 59,999 | 59,999 | 179,997 |
| AI and vision (Pi 5, AI HAT+, Camera 3 Wide, gimbal) | 39,121 | — | 39,121 |
| Power (PM07, UBEC, battery) | 22,949 | 22,299 | 67,547 |
| Navigation (M10 GNSS) | 6,824 | 6,824 | 20,472 |
| Telemetry (RFD868x) | 18,150 | 18,150 | 54,450 |
| Precision drop (TFmini-S, 2x MG90S, break-beam allowance) | — | 3,900 | 7,800 |
| Payload bay (magazine and escapement allowance) | — | 2,500 | 5,000 |
| Structure (frame, landing gear, fasteners and brackets) | 4,450 | 6,650 | 17,750 |
| **Drone subtotal (BOM)** | **176,039** | **157,141** | **490,321** |
| GCS | | | 35,900 |
| 10 medical kits | | | 5,000 |
| **BOM subtotal** | | | **531,221** |
| FPV video sets (RunCam ₹1,999 + TX800 ₹2,965 = ₹4,964 per drone) | 4,964 | 4,964 | 14,892 |
| **Total** | | | **546,113** |

- Still to be priced: 3 ground 5.8 GHz receivers, 3 USB capture devices, the RF and servo regulators, harness materials, an RC safety link for development flights, and the ground RFD868x modem if it is not already inside the ₹35,900 GCS line.
- The DEL frame (₹3,800), magazine (₹2,500) and break-beam (₹1,500) are allowances, not vendor quotes.
- The three Cube Orange+ sets are about 33% of the total cost.

## 10. Test plan

| # | Test | Method | Pass criterion | Owner | Status |
|---|---|---|---|---|---|
| T1 | Weigh-in | Calibrated scale: each part, then each complete aircraft with battery, then all 10 kits | Fleet 25 kg or less measured; each aircraft within +10% of 3.987 / 5.414 kg, otherwise the power model is re-run | Mechanical | PENDING TEST |
| T2 | Thrust stand | TL2955 + 1555 prop + 40 A ESC on a real 6S pack; steps from 0 to 100%; log thrust, current, voltage, rpm, motor and ESC temperature | At least 2,000 g at full throttle at pack voltage. Hover-point power within ±15% of 88.6 W (INS-01) and 77.3 W (DEL). Motor temperature stable in a 15-min run at 997 g and 902 g | Electrical | PENDING TEST |
| T3 | Power bench | PM07-to-Cube continuity and pinout; regulator outputs under peak load (Pi 5 + Hailo-8, servo stall, RF transmit) | Correct voltages, no brownout, no connector heating | Electrical | PENDING TEST |
| T4 | 50-release bench test | Loaded magazine on the DEL frame with motors running (vibration), level and at 15° tilt (ASSUMED test angle) | 50 out of 50 single releases, 0 double releases, 0 jams, 50 out of 50 break-beam detections, 0 false triggers | Mechanical + embedded | PENDING TEST |
| T5 | Tethered hover | Each aircraft on a tether, logging power and vibration | Hover power within ±15% of 385.2 W / 476.0 W | Testing lead | PENDING TEST |
| T6 | Drop accuracy | 10 drops from 3 m over a marked point | Scatter measured and compared with the 0.39–1.56 m drift model; kit undamaged | Testing lead | PENDING TEST |
| T7 | Detection and geotag | Markers and dummies at surveyed points; lanes flown at 12 m and 8 m/s | Recall, false alarms and geotag error measured; geotag error compared with the 5 m merge radius | AI / vision | PENDING TEST |
| T8 | Links | 4-node RFD868x range and latency; 3 video channels and 3 capture panels running together | Full-field coverage, no video cross-talk, all panels live | Embedded + GCS | PENDING TEST |
| T9 | Failsafes | Link loss, low battery, geofence, EMERGENCY RECALL; in simulation first, then in the field | Correct return at each drone's layer altitude | Embedded + GCS | PENDING TEST |
| T10 | Mission rehearsal | 5-min setup, START only, no manual inputs | Mission time measured against the 14–15 min estimate | All | PENDING TEST |

## Evidence status

| Item | Status | Note |
|---|---|---|
| Airframe geometry and propeller clearance (79 mm / 69 mm) | DESIGNED + CALCULATED | CAD; gap with motors running not yet measured |
| Masses 3.987 kg and 5.414 kg | CALCULATED | BOM figures; magazine, landing gear, harness and RF regulator not included; nothing weighed |
| Thrust curve (790 / 1,510 / 2,200 g) | ASSUMED | Unverified reference data at 24 V |
| Hover thrust, T/W and hover power | CALCULATED | From the reference curve |
| 70% usable battery energy | ASSUMED | To be measured |
| Endurance figures | CALCULATED | From the above |
| Power architecture and wiring | DESIGNED | Workbook wiring table |
| PM07-to-Cube pinout | PENDING TEST | Must be checked before power-up |
| Escapement concept and servo check | DESIGNED + CALCULATED | Friction coefficient ASSUMED |
| Lua break-beam script | DESIGNED | Not yet bench-tested |
| Drop physics | CALCULATED | Drag ignored |
| Materials and manufacturing | DESIGNED | No parts fabricated yet for the final design |
| GCS software | BUILT-SIMULATED | 3-drone version in adaptation |
| Cost | CALCULATED | BOM prices; some lines are allowances |
| T1–T10 | PENDING TEST | No test results yet |

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| DEL is 8.3% above Tarot's hexa guidance, and some mass is not yet counted | Motor overheating, smaller control margin | Thrust-stand temperature run at 902 g per motor; weigh-in; remove about 414 g if needed (lighter battery after power data, magazine panels, frame plates) |
| Reference curve is at 24 V and may be optimistic | Lower T/W (about 1.89 / 2.09 at 22.2 V), more power | Thrust stand at real pack voltage. At +30% power, endurance is still 29.1 min or more on the mission profile |
| PM07-to-Cube pinout not verified | Autopilot damaged at first power-up | Manufacturer harness, or continuity and voltage checks before connecting |
| ~30 A stock PM07 lead against a ~71 A peak | Lead or connector overheating | 10 AWG wire and XT90-S connector; temperature check in T2 |
| Pi 5 brownout at Pi + Hailo-8 + camera peak | Detection stops mid-search | Dedicated 5 V / 5 A UBEC; measure the peak; short, thick supply lead |
| Escapement jams or releases two kits | Kit not delivered, or two kits at one survivor | 2-stage gates; 50-release test; break-beam check; retry and re-assign |
| Landing legs in the 102° camera view | False detections or blocked view | Check the field-of-view cone in CAD; move the legs or mask that image region |
| Hailo-8 heating or low frame rate | Missed detections | Benchmark frame rate and temperature; heatsink and airflow |
| Video transmitter overheating while a DEL waits on the pad | Video loss | Low power on the ground; heat sink; temperature check during the wait |
| Interference between 868 MHz, 5.8 GHz and GNSS | GNSS degradation or link loss | Antenna separation, wire routing, ground interference test |

## Likely judge questions

**Q1. Your delivery hexa is above the motor maker's ~5 kg guidance. Why fly it?**
On paper it is 5.414 kg, 8.3% over, and the magazine and landing gear mass are still to be added. On the reference curve hover needs 41% of maximum thrust and T/W is 2.44. But a calculation cannot replace the maker's guidance, so we will run the thrust stand at 902 g per motor for longer than a mission while logging motor temperature. If it fails, we cut mass. About 414 g brings the DEL to 5 kg.

**Q2. Can an MG90S micro servo carry the kits?**
No, and it does not have to. Holding the 1 kg stack on a 2 cm horn would need 2.0 kgf·cm at 1 g and about 4 kgf·cm at 2 g, against a stall torque of 2.2 kgf·cm. In our design the stack load goes through the gates into the magazine walls. The servo only slides a latch against friction, about 0.5 kgf·cm with an assumed friction coefficient of 0.3. The 50-release bench test will show whether this holds.

**Q3. How indigenous is the system?**
By cost, about 88% is imported electronics bought through Indian distributors. That includes the Cube Orange+, Raspberry Pi, Hailo, RFD868x, Tarot, Benewake, Herewin, RunCam, SpeedyBee and TowerPro parts. Our own work is the engineering: airframe CAD and CNC plates, motor mounts, gimbal plate, magazine and escapement, the GCS, allocation logic, geotag pipeline, Lua script, and the dataset and fine-tuning. The roadmap is in-house INS-01 plates next, then Indian-made packs, ESCs and other parts once they pass the same tests.

**Q4. Endurance is about three times the mission. Why carry a 2.35 kg battery, and are these figures measured?**
They are not measured. They come from an unverified reference curve and an assumed 70% usable energy, and a momentum-theory check suggests the curve may be optimistic. We keep the 22 Ah pack until the thrust stand and tethered hover give real power figures. Even with 30% more propulsion power, hover endurance is 41.7 min (INS-01) and 33.3 min (DEL). Once we have measured data, a lighter battery is the first way to bring the DEL under 5 kg and raise T/W.
