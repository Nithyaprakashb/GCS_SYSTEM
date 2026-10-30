# Fail-Safe Features and Safety — 15 marks

**NIDAR 2026 · Problem Statement 1 · Phase 4A design review · Rubric row 9**
**Applies to:** INS-01, DEL-01 and DEL-02 (each has its own Cube Orange+ running ArduPilot and its own independent RC safety receiver), plus the GCS
**Status in one line:** the fail-safes are designed and their parameters chosen, but none has been tested in SITL or in flight yet.

## Key message

Every drone protects itself in its own autopilot, so a geofence breach, link loss, low battery, RC loss or EKF/GNSS failure ends in RTL or LAND even if the GCS laptop dies. On top of that, the GCS adds fleet-level safety: altitude layers, a 20 m descent rule, one drone on the pad at a time, and a kit ledger that never releases a kit twice.

---

## Slide plan

### Slide 1 — "All six required fail-safes, on every drone"
- RTL, low battery, comm loss, geofence, abort and emergency recall are implemented on INS-01, DEL-01 and DEL-02.
- They run in ArduPilot on each Cube Orange+. The GCS writes them during setup and checks them.
- The geofence is the organiser KML polygon plus an altitude limit; a breach triggers RTL.
- GCS link loss -> RTL. Low battery -> RTL. Critical battery -> LAND. EKF/GNSS failure -> LAND.
- EMERGENCY RECALL (all RTL, sent repeatedly) and ABORT (land) are the only in-flight operator controls.
- *Suggested visual:* the fail-safe matrix from Section 2 as a coloured table, with fail-safes as rows and INS-01 / DEL-01 / DEL-02 as columns.

### Slide 2 — "Three drones, one field, one pad: separation by design"
- The drones fly in layers: INS-01 searches at 12 m, DEL-01 transits at 18 m and DEL-02 at 22 m.
- Each has its own RTL altitude: INS-01 25 m, DEL-01 18 m, DEL-02 22 m.
- A DEL drone descends to 3 m only when no other drone is within 20 m of the drop point.
- Pad interlock: one drone takes off or lands at a time, and DEL drones wait disarmed until they are assigned.
- Kit ledger: a kit counts as delivered only when the break-beam sees it leave. If there is no beam it is marked UNKNOWN and never re-fired.
- *Suggested visual:* the side-view altitude-layer diagram from Section 4, with the 20 m clearance zone drawn around the drop point.

### Slide 3 — "When something fails, and how we will prove it"
- If INS-01 is lost, the DEL drones finish the deliveries already confirmed, then RTL.
- If a DEL drone is lost, its undelivered survivors are reassigned to the other DEL drone.
- If the GCS laptop crashes, every airborne drone does a link-loss RTL, and no kit can be released without the GCS.
- Automatic pre-flight checks run for each role, and START stays locked until all of them pass.
- Test ladder: bench (props off) -> SITL x3 with injected failures -> tethered / low altitude -> full rehearsal.
- *Suggested visual:* the failure-response table from Section 7 next to the four-step test ladder from Section 10.

---

## 1. Safety layers

```text
 Layer 5  Procedures       pre-flight checks, test ladder, WPC / venue permission, briefing
 Layer 4  Operator         EMERGENCY RECALL (all RTL) and ABORT (land): the only in-flight inputs
 Layer 3  GCS fleet logic  pad interlock, 20 m descent rule, kit ledger, reassignment, warnings
 Layer 2  Autopilot        ArduPilot fail-safes on each Cube Orange+ (work without the GCS)
 Layer 1  Safety pilot     independent RC receiver on every drone: takeover at any time
```

The lower layers do not depend on the upper ones. The autopilot fail-safes work without the GCS laptop, and the RC safety link works without the telemetry link.

## 2. Required fail-safe matrix

| # | Fail-safe | Trigger | Action | Runs on | ArduPilot setting (DESIGNED) | INS-01 | DEL-01 / DEL-02 |
|---|---|---|---|---|---|---|---|
| 1 | **Geofence** | Drone reaches the KML polygon or the altitude limit | RTL (LAND if RTL is not possible) | Autopilot | FENCE_ENABLE = 1, FENCE_TYPE = 5 (polygon + max altitude), FENCE_ACTION = 1 | Yes | Yes |
| 2 | **Comm loss** (GCS link) | No GCS heartbeat for the timeout (FS_GCS_TIMEOUT, default 5 s) | RTL | Autopilot | FS_GCS_ENABLE = 1 | Yes | Yes. On the pad the drone stays disarmed |
| 3 | **Low battery** | Battery below the low threshold | RTL | Autopilot | BATT_LOW_VOLT, BATT_FS_LOW_ACT = 2 | Yes | Yes. The GCS also reassigns its queue |
| 3b | Critical battery | Battery below the critical threshold | LAND | Autopilot | BATT_CRT_VOLT, BATT_FS_CRT_ACT = 1 | Yes | Yes |
| 4 | **RTL** | End of mission, or any trigger in this table | Climb to its own RTL altitude, fly to its own pad spot, land | Autopilot | RTL_ALT = 2500 / 1800 / 2200 cm | 25 m | 18 m / 22 m |
| 5 | **Abort** | Operator, from the emergency group | Land in place | GCS -> autopilot | LAND mode command | Yes | Yes. Any pending release is cancelled |
| 6 | **Emergency recall** | Operator, two clicks within 4 s | All drones RTL. The command is sent repeatedly until each heartbeat shows RTL | GCS -> autopilot | RTL mode command to all three | Yes | Yes. Any pending release is cancelled |
| + | RC loss | The safety receiver loses its signal | RTL | Autopilot | FS_THR_ENABLE = 1 | Yes | Yes |
| + | EKF / GNSS failure | EKF variance too high, or GNSS lost | LAND | Autopilot | FS_EKF_ACTION = 1 | Yes | Yes |

**Notes**
- The GCS writes these parameters to each drone during setup and reads them back. Any mismatch fails the pre-flight check.
- The GCS sends its heartbeat with the system ID the autopilots expect (SYSID_MYGCS), so the link-loss timer measures the real GCS link.
- When several fail-safes are active, ArduPilot keeps the more conservative action, so a LAND is not replaced by an RTL.
- The battery thresholds will come from a measured discharge curve of the 6S 22 Ah pack (PENDING TEST).
- The default design for RC loss is RTL. After RC range tests we will decide whether AUTO / GUIDED should continue on RC loss (FS_OPTIONS).
- ArduPilot's own pre-arm checks stay enabled on all three drones.

## 3. Geofence and boundary margin

| Item | Distance inside the boundary | Purpose |
|---|---|---|
| Autopilot fence (breach -> RTL) | 0 m (the KML polygon itself) | Hard limit on every drone |
| GCS boundary warning | 2 m | Early alert before a breach |
| INS-01 lane ends | 3 m | Turns happen inside the fence |
| INS-01 outer lanes (500 x 200 m model) | about 11.1 m | The camera still sees the edge strip |

- The KML polygon is uploaded as an inclusion fence to all three drones and checked after upload.
- The maximum-altitude fence sits above the highest RTL altitude (25 m) with a margin; its exact value will follow the event rules.
- The GCS checks every lane end and every delivery target against the polygon before sending it.
- Survivors near the boundary: if a geotag is within 2 m of the boundary, the GCS moves the drop point inward so the drone stays inside the fence. This trades a little drop accuracy for avoiding the -20 breach penalty (proposed; DESIGNED).
- A fence breach also triggers RTL and ends that drone's task, so we rely on prevention (insets, checks and warnings) and treat the fence as the last line of defence.

## 4. Separation between the three drones

```text
 height AGL
   25 m  ..................................  INS-01 RTL altitude (returns above everyone)
   22 m  ==================================  DEL-02 transit + RTL layer
   18 m  ==================================  DEL-01 transit + RTL layer
   12 m  ----------------------------------  INS-01 search layer (lanes)
    3 m             |  DEL descends to 3 m only if no other drone is within 20 m
    0 m  [ 12 ft pad ]  one drone taking off or landing at a time

                   <------- 20 m ------->  clearance radius around the drop point
```

| Rule | Value | Purpose |
|---|---|---|
| Vertical layers | 12 / 18 / 22 m, plus 25 m for the INS-01 RTL | 3–6 m between layers in cruise and on return |
| Clearance for descent and climb-out | No other drone within 20 m horizontally | A DEL drone crosses INS-01's 12 m layer only when the area is clear |
| Proximity warning | Any pair of drones closer than 8 m | GCS alert in the timeline |
| Pad interlock | One drone on or over the pad at a time | No crowding on the 12 ft pad |
| DEL launch hold | DEL drones stay disarmed until assigned and the pad is clear | Fewer drones in the air |

INS-01 flies at 8 m/s, so it crosses a 40 m clearance circle in about 5 s. The descent rule therefore costs very little time (CALCULATED).

## 5. Pad safety (12 ft pad)

- **Pad interlock:** the GCS lets only one drone take off or land at a time. A DEL launch waits until the pad is clear, and returning drones land in sequence.
- **Own spot and home:** each drone has a marked spot on the pad. Its home position is set when it arms on that spot, so RTL brings it back there.
- **DEL drones stay disarmed** on the pad until their first assignment.
- **Different RTL altitudes** (25 / 22 / 18 m) keep returning drones apart. In a normal finish, the GCS orders the final descents one at a time. For a return after a GCS crash, when the GCS cannot sequence them, we propose a different RTL loiter time per drone (RTL_LOIT_TIME) so they do not start the final descent together.
- **Footprint check (CALCULATED):** a DEL hexa measures about 1.28 m from prop tip to prop tip (900 mm frame + 15 in props), and INS-01 about 1.03 m. Side by side the three need about 3.6 m against a 3.66 m (12 ft) pad. The spot layout and the landing accuracy must therefore be checked on a full-size mock pad (PENDING TEST).

## 6. Delivery safety (DEL-01 / DEL-02)

**A kit is released only when all of the following are true:**
1. The drone is armed, in GUIDED mode, and over its assigned survivor.
2. It is at 3 m AGL on the TFmini-S, within tolerance.
3. It has passed hover-verify (position, speed and height steady). After one failed re-approach, it releases only inside the 1 m ring.
4. A kit is reserved for this survivor in the ledger.
5. The GCS sends the release command (DO_SET_SERVO). Nothing on the drone releases a kit by itself.

**Kit ledger (one entry per kit)**

| State | Meaning | Counted as delivered? | Free for a new survivor? |
|---|---|---|---|
| LOADED | In the gate, not assigned | No | Yes |
| RESERVED | Assigned to a queued survivor | No | No |
| RELEASE_SENT | Release sent, waiting for the beam | No | No |
| **DELIVERED** | Break-beam confirmed the kit left | **Yes** | No |
| **UNKNOWN** | Release sent, no beam seen; never re-fired | No (flagged) | No |
| RETURNED | Still on board at landing | No | No |
| LOST | On a drone that failed in the air | No | No |

- **Never a double release:** if there is no beam, the kit is marked UNKNOWN and is not fired again. This avoids both two kits on one survivor and a false DELIVERED.
- The gate servo is tested **before the kits are loaded**, never with kits in the gate.
- If the link is lost during a delivery, the drone does RTL without releasing, because a release needs a GCS command.
- A DEL drone is **not launched** unless it has a free kit, enough battery for its queue plus RTL with reserve, and a healthy link.

## 7. Failure handling at fleet level

| Failure | Immediate action (autopilot) | Fleet action (GCS) | Effect on the mission |
|---|---|---|---|
| INS-01 lost (link, battery, fault) | RTL or LAND by its own fail-safe | DEL drones finish the deliveries already confirmed, then RTL | No new detections; confirmed survivors are still served |
| DEL-01 or DEL-02 lost | RTL or LAND by its own fail-safe | Its undelivered survivors go back to the allocator and are reassigned to the other DEL drone if it has free kits. Its kits are removed from the pool | The other drone's free kits are still delivered |
| No free kit left anywhere | — | The survivor is marked UNSERVED and stays on the map with its geotag | Shown honestly in the survivor list |
| GCS laptop crash or power loss | Every airborne drone does a link-loss RTL after the timeout; DEL drones on the pad stay disarmed | — (logs are written during the mission, so the record up to the crash is kept) | Mission ends safely with no uncommanded release |
| Telemetry degraded | — | Link light turns amber and a warning appears | Operator is aware before a fail-safe triggers |
| GNSS / EKF failure | LAND | Timeline entry and warning; the RC safety pilot is ready to take over | The drone lands where it is |
| AI down on INS-01 | — | Alarm; a watchdog restarts the detector; lanes flown meanwhile are shown as unsearched | Possible missed survivors, shown honestly on the heatmap |
| Video lost on a panel | — | VIDEO OFFLINE on that panel | No effect on flight |
| Release not confirmed | — | Kit marked UNKNOWN, never re-fired | Ledger stays correct |
| Emergency (operator) | — | EMERGENCY RECALL (all RTL) or ABORT (land) | Allowed input, used only for safety |

## 8. Automatic pre-flight checks (START is locked until all pass)

| Check | INS-01 | DEL-01 / DEL-02 |
|---|---|---|
| Telemetry link (heartbeat, RSSI) | Yes | Yes |
| GNSS | 3D fix, 8 or more satellites | 3D fix |
| Battery | Yes | Yes |
| Geofence uploaded, matches the KML, enabled | Yes | Yes |
| Mission / parameters uploaded and checked | Search mission | Delivery and fail-safe parameters |
| Pi 5 and AI detector running | Yes | — |
| 5 kits loaded and break-beam OK | — | Yes |
| Rangefinder (TFmini-S) gives a valid reading | — | Yes |
| Gate servo tested (before kits are loaded) | — | Yes |
| On its pad spot, inside the boundary | Yes | Yes |
| Disarmed | Yes | Yes |

That is 8 checks for INS-01 and 10 for each DEL drone. No check can be passed by clicking it.

## 9. Energy reserve for a safe return (CALCULATED on an unverified thrust curve)

| | INS-01 | DEL (loaded) |
|---|---|---|
| Mission-profile power | 438 W | 546 W |
| Usable energy | 341.88 Wh (assumed 0.70 x 488.4 Wh nameplate) | 341.88 Wh |
| Worst-case RTL: farthest corner about 540 m at 10 m/s, plus 30 s to climb, descend and land (about 85 s, ASSUMED) | about 10 Wh (3 %) | about 13 Wh (4 %) |
| Energy for a full 15 min in the air | about 110 Wh (32 %) | about 136 Wh (40 %) |

A low-battery RTL threshold that still leaves 20–30 % of usable energy would give several times the energy needed to get home. The final thresholds will come from the discharge test (PENDING TEST).

## 10. Test plan (fail-safes are proven before any full mission)

```text
 1 BENCH (props off) --> 2 SITL x3 + injected failures --> 3 TETHERED / LOW ALTITUDE --> 4 FULL REHEARSAL
```

| Fail-safe | 1 Bench | 2 SITL x3 | 3 Tethered / low | 4 Rehearsal | Pass criterion (target) |
|---|---|---|---|---|---|
| Geofence -> RTL | Parameters read back | Breach a test fence | Small test polygon | No breach expected | RTL on breach, logged |
| Comm loss -> RTL | Ground radio off: mode change in log | Kill the GCS link | Ground radio off | — | RTL after the timeout; no release |
| Low / critical battery | Threshold raised above pack voltage | Simulated voltage drop | Raised threshold, short flight | — | RTL, then LAND as configured |
| RC loss | Transmitter off | — | Transmitter off at low altitude | — | RTL |
| EKF / GNSS failure | — | GNSS disabled in simulation | Not induced in flight | — | LAND |
| Emergency recall / abort | Command reaches all three | All three RTL in sequence; abort lands | Single drone first, then a pair | Once, at the end | Every drone in RTL / LAND; pending release cancelled |
| Pad interlock and staggered landing | — | Three drones finish together | A pair of drones | Yes | Never more than one drone on the pad at a time |
| Descent deconfliction | — | INS-01 path crosses a drop point | — | Yes | DEL waits until there is 20 m clearance |
| Kit ledger | Beam blocked / not blocked | Simulated missing beam | Real drop over a soft target | Yes | Missing beam gives UNKNOWN, no re-fire |
| DEL lost -> reassignment | — | Kill DEL-02 mid-queue | — | — | Queue moves to DEL-01 |
| GCS crash | — | Kill the GCS process | Laptop closed at low altitude | — | Every airborne drone does RTL |

The pass criteria are targets, not results. We will report results only after the tests.

## 11. Radio permissions and field procedures

- The 5.8 GHz analog video (three TX800 transmitters) and the 868 MHz telemetry (RFD868x) need **WPC and venue permission**. Channels and power will be agreed before any flight at the venue.
- Video uses three channels spread well apart, each at the lowest power that gives a clean picture. The RFD868x is set to the frequency range that is permitted; in India the licence-exempt band near 868 MHz is 865–867 MHz, which we will confirm.
- Each drone has a safety pilot holding that drone's RC transmitter, with an RC range check before flight.
- Field procedures: pre-flight checks by the GCS, a safety briefing, people kept clear of the pad, and battery handling and charging rules.

---

## Evidence status

| Item | Status |
|---|---|
| Fail-safe set and ArduPilot parameter choices (fence, GCS link, battery, RC, EKF, RTL altitudes) | DESIGNED |
| Emergency recall (two clicks within 4 s) and START lock in the GCS | BUILT-SIMULATED (GCS core, run on an earlier fleet configuration) |
| Repeated recall command to all three drones, abort (land), pad interlock, 20 m descent rule | DESIGNED (being coded) |
| Kit ledger with UNKNOWN / no re-fire; Lua break-beam script | DESIGNED |
| Per-role automatic pre-flight checks | DESIGNED (the pre-flight gate is BUILT-SIMULATED) |
| RTL energy need (3–4 %) and 15-min energy use (32 % / 40 %) | CALCULATED on an unverified thrust curve; usable energy ASSUMED |
| Pad footprint (about 3.6 m of drones against a 3.66 m pad) | CALCULATED from frame and prop sizes |
| Battery thresholds | PENDING TEST (discharge curve) |
| Every fail-safe in SITL x3 and in flight | PENDING TEST |
| WPC / venue permission for 5.8 GHz and 868 MHz | PENDING |

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| A long telemetry dropout triggers link-loss RTL | INS-01's search ends early | Check the link budget and antenna placement; tune stream rates for three drones; set the timeout in SITL; amber link warning |
| RTL climb-outs cross other layers (a DEL drone climbing from 3 m passes through the 12 m layer) | Brief vertical conflict | RTL climbs straight up at the drone's own position; the 20 m rule normally holds; the 8 m proximity warning; worst cases tested in SITL; RC takeover |
| All drones return together after a GCS crash | Crowded pad | Different RTL altitudes; different RTL loiter times (proposed); safety pilots ready |
| Tight pad footprint (about 3.6 m of drones on a 3.66 m pad) | Prop contact while landing | Triangle spot layout; one drone moving at a time; measure landing scatter on a mock pad |
| A survivor close to the boundary | A breach while dropping (-20) | Move the drop point inward when the geotag is within 2 m of the boundary (proposed); 2 m warning |
| Battery thresholds not yet measured | RTL too early or too late | Discharge test; start with conservative thresholds |
| EKF LAND without GNSS drifts with the wind | Lands off target | 8 or more satellites before arming; RC takeover in a non-GNSS mode |
| Video or telemetry band not permitted at the venue | No live video or no telemetry | Apply for permission early; alternative permitted channels |
| No fail-safe tested yet | Unknown behaviour | Follow the test ladder in order; no full mission before stages 1–3 pass |

## Likely judge questions

**Q1. What happens if your laptop crashes mid-mission?**
Every airborne drone loses the GCS heartbeat and does an RTL after the timeout, at its own RTL altitude. DEL drones on the pad stay disarmed, and no kit can be released because only the GCS can command a release.

**Q2. How do you stop three drones colliding?**
They fly in separate altitude layers (12 / 18 / 22 m, with 25 m for the INS-01 return). A DEL drone descends only when no other drone is within 20 m, only one drone uses the pad at a time, and the GCS warns below 8 m. This is designed but not yet tested in SITL.

**Q3. Can a kit drop by accident, or twice?**
No release happens without a GCS command at 3 m after hover-verify. A missing beam is marked UNKNOWN and never re-fired, and a lost link sends the drone home with its kits on board.

**Q4. Have you tested these fail-safes?**
Not yet. They use ArduPilot's standard fail-safe functions, configured and checked by our GCS. We will test them in order from bench to SITL to tethered flight to full rehearsal, and report only measured results.
