# ADRODS-X — Ground Control Station, Mission Map & Multi-Drone Status Reporting

**Phase 4A rubric row 8 · 20 marks**
**Software:** ADRODS-X GCS (Windows desktop app, fully offline)
**Evidence status:** `BUILT + SIMULATED` — runs end to end against simulated drones over real MAVLink. Not yet run on ArduPilot SITL or the real aircraft; the digital video link is not yet selected.

> One GCS shows both hybrid drones at the same time: one mission map, one status card per drone, two live video panels, and one shared survivor list. The operator has only two buttons: **START MISSION** and **EMERGENCY RECALL**.

---

## 1. How data reaches the screen

```text
HYB-01 / HYB-02                     GCS laptop (offline, no internet)
Cube Orange+ (ArduPilot) ──┐
Pi 5 + Hailo (AI, geotag) ─┼─ RFD868x, MAVLink 2 ──► GCS server (Python) ──► GCS screens (React + MapLibre)
Pi Camera 3 Wide ──────────┘   digital video link ─────────────────────────► Live video panels
                               (model TBD)
```

| Stage | What happens | Rate |
|---|---|---|
| Telemetry in | Heartbeat, position, attitude, battery, GNSS, radio RSSI, rangefinder, mission item | ArduPilot stream rates |
| Drone ⇄ GCS mission data | Survivor detections, assignments, drop reports, recall (MAVLink `TUNNEL` messages) | event-driven |
| Screen update | Server pushes the whole fleet state to the screens | 5 Hz |
| Mission record | Telemetry written to CSV | 2 Hz |

The same software runs in **SIMULATION** and **REAL** mode. A badge in the top bar always shows which one; the simulation never fills in for real hardware that is missing.

---

## 2. The four screens

| Screen | What it is for |
|---|---|
| **Mission** | Everything on one screen: drone cards, compact map, both live feeds, survivors, timeline, score |
| **Tactical map** | Full-size map with the analysis panel |
| **Live video** | Both camera feeds, large, side by side |
| **Log** | Full event history with search; simulator settings before launch |

Dark and light themes (light for outdoor sunlight).

---

## 3. Mission map

Built with MapLibre GL. Works **with no internet and no map tiles**; everything is drawn from the organiser's KML and live telemetry.

| Layer | What it shows | Why it matters |
|---|---|---|
| Mission boundary | Polygon from the organiser KML (dashed) | Same polygon is uploaded as the autopilot geofence |
| Sectors | Two equal-area territories, HYB-01 blue, HYB-02 orange | Shows the work split at a glance |
| Search lanes | Planned lawnmower lanes, 13.83 m apart, ends kept 3 m inside the boundary | Proves full coverage was planned without crossing the fence |
| Drone markers | Position, heading, altitude, task label | Live location of both drones |
| Trails | Path flown by each drone | Shows what was actually flown |
| Camera footprints | Ground area each camera sees right now | Links video to the map |
| **Probability-of-detection heatmap** | Each 5 m cell coloured by the chance a person there would have been found (1 − (1 − p)^passes) | Shows search *quality*, not just "camera passed over" |
| **Coverage gaps** | Largest unsearched areas, with size and coordinates | Shows what is still missing |
| **Live mosaic** | Aerial picture of the field built from the drones' own video frames and position | A real image of the area without internet |
| Survivors | Numbered markers coloured by status (confirmed, assigned, delivered, unknown) | One shared, de-duplicated list |
| Uncertainty and drop rings | Geotag error circle, and the 1 / 2 / 3 m delivery zones around each survivor with the drop point | Shows accuracy honestly |
| Layer switches | Each layer can be turned on or off; "fit boundary" button | Keeps the map readable |

---

## 4. Multi-drone status reporting

### 4.1 Drone status card (one per drone)

| Field | Content |
|---|---|
| Identity | HYB-01 / HYB-02 in its own colour |
| Task | Standby, Searching, Delivering, Dropping kit, Resuming lane, Returning, Landed |
| Flight mode | ArduPilot mode (AUTO, GUIDED, RTL, LAND) and armed / disarmed |
| Battery | Voltage, percentage, energy used (Wh) |
| Altitude | Altitude and rangefinder height above ground |
| Speed | Ground speed and heading |
| GNSS | Satellites and HDOP |
| Radio | Signal strength at the GCS and at the drone |
| Kits | Kits left out of 5 |
| Progress | Search lanes done (%) |
| Prediction | Time to finish own work, and battery endurance left |
| Health | Pi 5 online, AI detector running, break-beam OK, mission + geofence loaded |

### 4.2 Fleet-level reporting

| Panel | Content |
|---|---|
| Top bar | Mission phase, mission clock against 30 min with the 15-min bonus marker, link light per drone, SIMULATION / REAL badge, **manual-input counter** |
| Survivor list | ID, status, coordinates, geotag error (± m), confidence, which drone saw it, which drone owns it, drop error |
| Autonomy timeline | Every automatic decision with its time: detections, assignments, re-assignments, drops, mode changes, failsafes |
| Tactical analysis | Area searched (%), mean probability of detection, predicted finish time, distance between the drones, each drone's distance to the boundary and battery reserve, largest gaps, warnings |
| Score panel | Phase 4D table, labelled **SIMULATED** or **ESTIMATE**. Official marks come from the judges |

### 4.3 Automatic warnings

- Drones closer than 8 m to each other
- A drone within 2 m of the boundary, or outside it
- Predicted finish later than the battery allows
- Lost telemetry link on either drone
- A drop with no break-beam signal (marked **UNKNOWN**, never re-fired)

---

## 5. Live video

- Two panels, one per drone, both visible at the same time (rule: live feed from every drone in one GCS).
- Overlay on each feed: drone ID, mode, altitude, height above ground, speed, heading, voltage, task.
- Each feed shows **VIDEO OFFLINE** clearly when its source is missing.
- In simulation the feed is a synthetic camera view, watermarked **SIMULATION · SYNTHETIC CAMERA**.
- Both feeds are **recorded automatically** from Start to the end of the mission.
- Real hardware: one **Pi Camera 3 Wide** per drone → Pi 5 encodes → local digital video link (**model not yet selected**) → GCS.

---

## 6. Pre-flight and operator controls

- **23 automatic pre-flight checks**: telemetry, GNSS fix and satellites, battery, geofence uploaded, mission uploaded, Pi online, AI running, 5 kits and break-beam, rangefinder, drone on the pad inside the boundary, disarmed, plan fits in 30 min. Nothing passes because someone clicked it.
- **START MISSION** is enabled only when every required check is green.
- After Start, **EMERGENCY RECALL** (two clicks within 4 s, all drones return to launch) is the only control. Everything else is locked.
- Every operator action is logged with its time; the counter shows **0 manual inputs** for an autonomous run.

---

## 7. Mission records

Each mission saves a folder automatically:

| File | Content |
|---|---|
| `telemetry.csv` | Both drones at 2 Hz |
| `events.jsonl` | Every event and decision |
| `plan.json` | Boundary, sectors, lanes, estimates |
| `survivors.json` | Final survivor list with drops |
| `score.json` | Score estimate and how it was computed |
| `operator_actions.json` | Every button press (audit) |
| `recordings/` | Both video feeds |

---

## 8. What is done and what is pending

| Item | Status |
|---|---|
| Mission map, all layers, analysis panel | `BUILT + SIMULATED` |
| Two drone cards, survivor list, timeline, warnings | `BUILT + SIMULATED` |
| Automatic pre-flight, Start / Recall only, audit log | `BUILT + SIMULATED` |
| MAVLink mission and geofence upload with acknowledgement | `BUILT + SIMULATED` |
| Two live video panels with recording | `BUILT` (simulated camera) |
| Dual-drone ArduPilot SITL test | `PENDING` |
| Real aircraft over RFD868x | `PENDING` |
| Digital video link (two simultaneous feeds) | `PENDING`: hardware not selected |

**Latest simulated mission:** 9–10 of 10 survivors found, all simulated drops within 1 m, 0 manual inputs, 0 geofence breaches, about 16 minutes. Simulated results, not flight evidence.

---

## 9. 60-second demo script

1. **Load KML** (or Sample): the map draws the boundary, the two sectors and the lanes; the plan estimate appears.
2. Point at the **23 automatic checks** turning green; **START MISSION** becomes active.
3. Press **START MISSION**: both drones launch with a short stagger and search their own sector.
4. Show the **probability-of-detection heatmap** filling in and the **gap list** shrinking.
5. When a survivor is confirmed, show the marker, its error ring, the owner drone, and the timeline entry. Then show the drop and its 1 / 2 / 3 m rings.
6. Point at the **0 manual inputs** counter and the **EMERGENCY RECALL** button: the only control left.

---

## 10. Likely judge questions

**Is this real telemetry?**
In the demo it is simulated drones speaking real MAVLink, and the screen says SIMULATION. The same code connects to the real aircraft through the RFD868x; that test is pending.

**How do you show two video feeds?**
Two panels side by side, one per drone, each with its own overlay and recording. On the aircraft, the Pi Camera 3 Wide feed goes over a local digital link; we have not selected that link yet.

**How does the operator know the area was searched properly?**
The probability-of-detection heatmap and the coverage-gap list show which cells were seen and how well, not just where the drones flew.

**What can the operator do during the mission?**
Only Emergency Recall. Everything else is automatic and locked, and every action is logged, so the manual-input count can be checked.
