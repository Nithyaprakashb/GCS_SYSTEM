# Introduction, Team and Domain Diversity — 10 marks

Team Adrods_X | NIDAR 2026-27 | RescueSwarm | Problem Statement 1 | Phase 4A design review

All performance numbers in this file are calculated or assumed. None of them has been checked yet on a thrust stand or in flight.

## Key message

Adrods_X is a student team covering seven engineering domains. We are building RescueSwarm, a fleet of three autonomous drones (one AI search quadcopter and two delivery hexacopters) run from one offline ground station. It is designed to find up to 10 survivors in a 10 ha disaster zone and drop a 200 g medical kit to each one, in a calculated mission time of about 14–15 minutes against the 30-minute cap.

## Slide plan

### Slide 1 — The problem: find survivors and get first aid to them fast

- Floods, cyclones, landslides and earthquakes cut roads and bridges. Ground teams lose time just reaching and searching the affected area.
- Early first aid changes outcomes. This is the "golden hour" idea in emergency care, so the time from search to supply is the number that matters.
- Drones can search open ground from the air without putting rescuers at risk, and can drop supplies before ground teams arrive.
- NIDAR Problem Statement 1 tests this: a 10 ha zone, up to 10 survivors, one 200 g kit each, a 30-minute cap, no internet and very little human input.
- Suggested visual: a sketch of the 500 m x 200 m zone with survivor markers, the 12 ft take-off pad and the GCS, plus one photo of a flood or landslide area (your own photo or a licensed one).

### Slide 2 — RescueSwarm at a glance

- INS-01 (650 mm quad) searches the whole 10 ha at 12 m AGL and 8 m/s. It detects people on board with YOLOv8n running on a Hailo-8 accelerator.
- DEL-01 and DEL-02 (900 mm hexas) carry 5 kits each and release them from 3 m AGL. An IR break-beam confirms each release.
- One offline GCS: MAVLink 2 telemetry over RFD868x, three live video panels, and only two buttons (START MISSION and EMERGENCY RECALL).
- Fleet mass is 14.814 kg against the 25 kg limit. Calculated mission time is about 14–15 min against the 30-min cap (not yet verified in flight).
- Hardware cost is ₹546,113 for three drones, the GCS, 10 kits and three FPV video sets. The ground video receivers are not yet priced.
- Suggested visual: three drone silhouettes and the GCS laptop, with the 868 MHz telemetry links and 5.8 GHz video links drawn.

### Slide 3 — Our team and how we work

- Seven domain roles: mechanical/airframe, electrical/power, embedded/autopilot, AI/vision, GCS software, testing/safety and field ops, and documentation/business.
- Every subsystem has one owner and one backup from a different domain.
- We work in weekly sprints with a fixed test slot. Work moves on only through test gates: bench, thrust stand, tethered hover, single-drone autonomy, then full mission.
- One shared workbook is the hardware source of truth. Every number carries an evidence label: CALCULATED, ASSUMED, DESIGNED, BUILT-SIMULATED or PENDING TEST.
- Suggested visual: the domain diversity matrix (roles x subsystems) and a gate timeline.

---

## 1. Problem context

### 1.1 Disaster search and rescue

After a flood, cyclone, landslide or earthquake, the first job is to find out where people are, and the second is to get basic aid to them. Both jobs are slow from the ground. Roads are blocked, water and debris hide people, and every person sent into an unstable area is also at risk. Emergency medicine uses the idea of the "golden hour": the sooner first aid arrives, the better the chance of survival. In a disaster the same logic applies to search. Every minute spent searching is a minute without aid.

### 1.2 Why drones

| Need in the first hours | Problem with ground-only response | What our drone fleet adds |
|---|---|---|
| Find people quickly | Teams on foot or in boats search slowly over debris, water and broken roads | One search drone covers 10 ha in a calculated 10.2 min of search flight |
| Keep rescuers safe | Entering unstable or flooded ground puts rescuers at risk | The first search needs nobody inside the zone |
| Locate people precisely | Verbal reports and landmarks are vague | Each survivor gets a GNSS-based geotag on the GCS map |
| Deliver first aid early | Supplies arrive only with the ground team | Delivery drones drop a 200 g kit to each survivor while the search is still running |
| Share the picture | Information arrives late and in pieces | Live video from every drone and an automatic mission log at the GCS |

### 1.3 The NIDAR task (Problem Statement 1)

| Requirement | Value | Rule reference (as recorded in our workbook; re-check against the latest rulebook) |
|---|---|---|
| Search area | 10 ha (100,000 m²); boundary KML handed over during the 5-minute setup | Track 1 problem statement; Rule 4.14 (setup window) |
| Survivors | Up to 10 | Rule 8.12 |
| Payload | One medical kit per survivor: 200 g, 20 x 10 x 5 cm | Rule 8.12 |
| Time | 30-minute cap; bonus for 15 minutes or less | Rule 4.41 |
| Fleet mass | 25 kg maximum combined take-off mass | Rule 8.9 |
| Communications | Offline only: no internet, cellular or cloud | Rule 7.4 |
| Ground station | Single GCS with live video from every drone | Problem statement |
| Human actions | Only load mission, start, and abort/recall; -50 per manual input | Problem statement |

### 1.4 How the task drives our design

| Task constraint | Design response |
|---|---|
| No internet | Offline GCS and map, on-board AI on the search drone, local RFD868x telemetry |
| Live video from every drone on one GCS | One analog 5.8 GHz video transmitter per drone on a separate channel, shown in three panels on the GCS |
| -50 per manual input | Only two buttons (START MISSION and EMERGENCY RECALL). Pre-flight checks, survivor assignment and landing are automatic |
| Up to 10 kits of 200 g | Two delivery hexas with a 5-kit magazine each. Kits are pooled, so either drone can serve any survivor |
| 30-minute cap, 15-minute bonus | A dedicated search drone covers 10 ha in 10.2 min while deliveries run in parallel |
| Fleet of 25 kg or less | Calculated fleet mass is 14.814 kg, a margin of 10.186 kg |
| 5-minute setup | Folding propellers, KML import, automatic pre-flight checks, one-click start |

## 2. Project summary

RescueSwarm is a three-drone fleet with one search drone and two delivery drones. INS-01, the search quadcopter, flies a lawnmower pattern over the whole zone and runs person detection on board. It confirms each survivor over three frames, geotags it and reports it to the GCS. The GCS merges duplicate reports and assigns each survivor to whichever delivery hexa can reach it soonest and still has a free kit. That hexa flies over the survivor at its own transit altitude, descends to 3 m, releases one kit and confirms the release with a break-beam sensor. All three drones then return and land one after another on the pad. The operator loads the mission, presses START, and only touches the system again for an emergency recall.

| Item | Value | Evidence status |
|---|---|---|
| Aircraft | INS-01 search quad (650 mm X4); DEL-01 and DEL-02 delivery hexas (900 mm flat X6) | DESIGNED |
| All-up mass | INS-01 3.987 kg; each DEL 5.414 kg loaded, 4.414 kg after 5 drops | CALCULATED (BOM + video set; not weighed) |
| Fleet mass | 14.814 kg against the 25 kg limit (margin 10.186 kg) | CALCULATED |
| Thrust-to-weight | INS-01 2.21, DEL 2.44 at the 100% point of the reference curve | CALCULATED (reference curve unverified) |
| Hover endurance | INS-01 53.3 min, DEL 43.1 min with 70% usable battery energy | CALCULATED + ASSUMED |
| Search time | 10.2 min (9 lanes x 500 m, 12 m AGL, 8 m/s) | CALCULATED |
| Mission time | About 14–15 min against the 30-min cap; bonus not claimed until measured | CALCULATED |
| Kits | 10 (5 per DEL), pooled across both delivery drones | DESIGNED |
| Cost | ₹546,113, plus ground video receivers and capture devices (to be priced) | CALCULATED from BOM |
| GCS software | Runs in simulation; the 3-drone version is being adapted now | BUILT-SIMULATED |
| Flight testing | None yet | PENDING TEST |

## 3. Team composition (template — fill in names)

Names are placeholders. Each role has one owner and one backup. The backup comes from a different role so that knowledge is not held by one person.

| Role | Name | Branch / year | Owns | Phase 4A/4B deliverables | Backup |
|---|---|---|---|---|---|
| Mechanical / airframe lead | [Name] | [Branch, Year] | Airframe CAD (650 mm X4, 900 mm X6), propeller clearance, motor mounts, landing gear, PETG gimbal plate, 5-kit magazine and escapement | CAD files, materials table, weigh-in sheet, 50-release bench rig | [Name] |
| Electrical / power lead | [Name] | [Branch, Year] | 6S power system, PM07, ESC wiring, UBEC and BEC regulators, RF and video regulators, harnesses, power budget | Wiring table, power budget, PM07-to-Cube pinout check, current and temperature logs | [Name] |
| Embedded / autopilot lead | [Name] | [Branch, Year] | Cube Orange+ (ArduPilot) setup, parameters, failsafes, TFmini-S, servo outputs, Lua break-beam script, RFD868x network | Parameter files, Lua script, SITL setups, failsafe test records | [Name] |
| AI / vision lead | [Name] | [Branch, Year] | Pi 5 + Hailo-8 pipeline, survivor dataset, YOLOv8n fine-tuning, tiling, 3-frame confirmation, camera calibration, geotag model | Dataset, model metrics, FPS and power benchmark, geotag error test | [Name] |
| GCS software lead | [Name] | [Branch, Year] | Electron app, Python server, MAVLink 2 handling, offline MapLibre map, video panels, allocation logic, automatic logging | GCS builds, simulation runs, setup-time rehearsal | [Name] |
| Testing / safety and field ops lead | [Name] | [Branch, Year] | Test plan and gates, safety checklists, LiPo handling, field permissions, flight log archive, range safety | Test cards, risk register, field logs | [Name] |
| Documentation / business lead | [Name] | [Branch, Year] | Reports, BOM and cost tracking, vendor follow-up, indigenisation record, compliance (radio approvals, rulebook) | Design review files, BOM, cost sheet, compliance checklist | [Name] |
| Team lead / systems integration (can be one of the above) | [Name] | [Branch, Year] | Interface control, schedule, go/no-go at each gate | Interface table, gate decisions | [Name] |
| Faculty mentor | [Name] | [Department] | Technical review and safety oversight | Gate sign-off | — |

## 4. Domain diversity

### 4.1 Domain diversity matrix

L = lead, S = support. Every subsystem has one lead and at least two supporting roles.

| Role \ Subsystem | Airframe and payload | Propulsion and power | Autopilot, firmware and radio | AI and vision | GCS and mission logic | Testing and safety | Docs, cost and compliance |
|---|---|---|---|---|---|---|---|
| Mechanical / airframe | L | S | | | | S | S |
| Electrical / power | S | L | S | | | S | |
| Embedded / autopilot | | S | L | S | S | S | |
| AI / vision | | | S | L | S | | |
| GCS software | | | S | S | L | S | |
| Testing / safety and field ops | S | S | S | S | | L | S |
| Documentation / business | S | S | | | | S | L |

### 4.2 Knowledge areas the project needs

| Domain | Skills used | Where it shows up in RescueSwarm |
|---|---|---|
| Aerospace / mechanical | Multirotor sizing, propeller clearance, structures, CNC and FDM manufacturing | Airframes, motor mounts, magazine and escapement, landing gear |
| Electrical / electronics | Power distribution, regulators, wire sizing, noise and grounding | 6S bus, PM07, ESCs, Pi supply, servo BEC, RF and video regulators |
| Embedded systems and control | ArduPilot, MAVLink, Lua scripting, sensor integration | Cube Orange+ setup, failsafes, TFmini-S, break-beam script |
| Computer vision and AI | Dataset building, YOLOv8n fine-tuning, edge inference, camera geometry | On-board survivor detection and geotagging on INS-01 |
| Software engineering | Electron, React, MapLibre, Python, MAVLink 2 | Offline GCS, planner, allocator, logging |
| RF and communications | 868 MHz telemetry, 5.8 GHz analog video, antenna placement | 4-node telemetry network, three video channels |
| Operations and safety | Test planning, checklists, LiPo safety, field procedures | Test gates, field tests, mission rehearsal |
| Management and business | BOM, costing, sourcing, indigenisation, compliance | Cost tables, vendor tracking, reports |

### 4.3 Interfaces owned jointly

Most integration problems happen where two domains meet. Each interface below has two named owners.

| Interface | Owners | Why it is shared |
|---|---|---|
| Pi 5 to Cube Orange+ serial link (MAVLink 2) | AI / vision + embedded | Detections need aircraft position, heading and time from the autopilot |
| Pi 5 power supply (5 V / 5 A UBEC) | Electrical + AI / vision | Pi 5 + Hailo-8 load peaks must be measured to avoid brownout |
| Break-beam Lua script and escapement | Embedded + mechanical | Servo timing and sensor placement depend on the magazine geometry |
| Three video channels and GCS panels | Embedded + GCS software + testing | Channel plan, receivers and capture devices must work at the same time |
| Mass budget | Mechanical + electrical + documentation | Every part added changes hover power, endurance and the 25 kg check |
| Mission timing | GCS software + testing | The 14–15 min estimate must be checked against rehearsal logs |

## 5. Working process

### 5.1 Weekly sprint

| Slot | Activity | Output |
|---|---|---|
| Start of week | Sprint planning: each owner picks tasks linked to the next test gate | Sprint task list |
| Mid-week | Build and integrate, plus an interface check against the interfaces table | Updated CAD, wiring, code |
| Test slot | Bench or field test using a written test card | Test log, raw data files |
| End of week | Review: results compared with the calculated values, workbook updated, evidence labels changed only on measured data | Updated workbook, risk register, next-week plan |

### 5.2 Test gates

A subsystem moves forward only when it passes its gate. Every gate below is still pending except G0, which is this review.

| Gate | Content | Exit criteria | Status |
|---|---|---|---|
| G0 Design review | Architecture, calculations, BOM, test plan | Phase 4A review complete | In progress (this report) |
| G1 Bench integration | Power-on of each aircraft, PM07-to-Cube pinout check, regulator outputs, servo and sensor checks | No brownout, correct voltages, all sensors read in ground station | PENDING TEST |
| G2 Thrust stand and weigh-in | Motor and propeller curve at 6S, complete aircraft masses | Reference curve confirmed or replaced; fleet 25 kg or less measured | PENDING TEST |
| G3 Tethered hover | Each aircraft hovers on a tether | Stable hover; measured power within ±15% of the calculated value | PENDING TEST |
| G4 Single-drone autonomy | INS-01 lane search with detection; DEL guided transit, 3 m descent, release | Search pattern flown; releases confirmed by break-beam | PENDING TEST |
| G5 Multi-drone | 3-drone simulation, then field: altitude layers, 20 m descent rule, three video channels | No separation violation; all three video panels live | PENDING TEST |
| G6 Full mission rehearsal | 5-min setup, START only, no manual inputs | Mission completed and time measured against the 30-min cap | PENDING TEST |

### 5.3 Evidence and change control

- The shared workbook is the single hardware source of truth (component tables, vendors, INR prices, wiring, sensor matrix). Every change records the date and the owner.
- Every number we present carries one of five labels: CALCULATED, ASSUMED, DESIGNED, BUILT-SIMULATED or PENDING TEST.
- A number changes from CALCULATED to measured only when there is a log file behind it.
- We do not claim the 15-minute bonus, any endurance figure or any accuracy figure until it has been measured.

### 5.4 Safety practice during development

- The safety lead can stop any test.
- LiPo packs are charged and stored in fire-safe bags, with a written charge and storage log.
- Bench tests run with propellers off until motor direction and failsafes are checked.
- First flights are tethered. Autonomous flights start only after G3.
- During development we need a manual safety-pilot link. An RC transmitter and receiver are not yet in the BOM and must be added for testing. In the competition they are not used, because manual inputs cost marks.

## Evidence status

| Item | Status | Basis |
|---|---|---|
| Task analysis and design drivers | DESIGNED | Problem Statement 1 and the rule references in our workbook |
| Three-drone architecture and roles | DESIGNED | This design review |
| Fleet mass 14.814 kg | CALCULATED | BOM masses + FPV video set + DEL break-beam and servo BEC; nothing weighed yet |
| Thrust-to-weight 2.21 and 2.44 | CALCULATED | Unverified reference thrust curve (workbook sheet 7) |
| Usable battery energy of 70% | ASSUMED | Planning value; to be measured |
| Search time 10.2 min, mission time about 14–15 min | CALCULATED | Lane geometry and assumed speeds |
| GCS software | BUILT-SIMULATED | Runs in simulation; the 3-drone version is being adapted |
| Airframes, magazine, escapement | DESIGNED | CAD and concept stage |
| Flight performance, detection, geotag and drop accuracy | PENDING TEST | Covered by test gates G2–G6 |
| Team roles and process | DESIGNED | Names to be filled in by the team |

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| Only one person understands a subsystem | Work stops if that person is unavailable | Named backup from another domain; weekly review; written test cards and code notes |
| Lead time for imported electronics (Cube Orange+, Tarot motors, Hailo-8, RFD868x) | Late build, test gates slip | Order long-lead items first; confirm stock with Indian distributors; keep a spares list |
| Too little test time before the next phase | Untested claims at review | Gate-based plan; freeze the design after G2; evidence labels stop over-claiming |
| Test accidents (22 Ah LiPo packs, 15-inch propellers) | Injury, damaged hardware | Safety lead with stop authority, LiPo protocol, propellers off on the bench, tethered first hovers, safety-pilot link during development |
| Radio approvals (865–867 MHz telemetry, 5.8 GHz video) | Links not permitted at the venue | Check the WPC equipment approval for each radio; confirm channels and power with the organisers |
| Documentation falls behind the build | Weak review evidence | Documentation lead owns the workbook and report files; logs are uploaded after every test |

## Likely judge questions

**Q1. Why three drones?**
Search and delivery run at the same time. INS-01 covers the 10 ha in a calculated 10.2 minutes while DEL-01 and DEL-02 deliver to survivors as soon as they are confirmed. Splitting 10 kits (2 kg) across two hexas keeps each delivery drone at a calculated 5.414 kg. The whole fleet is 14.814 kg against the 25 kg limit.

**Q2. What have you actually built and tested?**
The GCS software runs in simulation, and we are adapting it to three drones now. The airframes, magazine and escapement are at CAD and concept stage. Nothing has flown yet. Every performance number in this review is calculated or assumed, and we label it that way. The first hardware gates are the thrust stand, the weigh-in and the 50-release bench test.

**Q3. How do you split the work and avoid integration problems?**
Each subsystem has one owner and one backup from a different domain. The interfaces where two domains meet, such as the Pi-to-Cube link or the break-beam script and the escapement, have two named owners. Weekly sprints end with a test slot, and work moves forward only through gates.

**Q4. How do you keep testing safe?**
A safety lead can stop any test. We follow a LiPo charging and storage protocol, run bench tests with propellers off, and fly tethered hovers before any free flight. Autonomous flight starts only after the tethered gate. During development we will use a manual safety-pilot link, which is not part of the competition setup.
