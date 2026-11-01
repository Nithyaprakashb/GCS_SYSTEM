# RescueSwarm GCS — MAVLink Mapping

Status of the REAL path: **BUILT / PENDING TEST**. The message encoders and the
mission protocol below are implemented and exercised against mocks in tests, but
no flight controller or RFD868x pair has been addressed by this build. Nothing in
this document is a test result.

- Protocol: MAVLink 2 over serial (default 57 600 baud) or UDP/TCP for benches.
- GCS identity: `source_system = 255` (`telemetry.gcs_sysid`), component 190.
- Autopilot assumed: ArduPilot Copter (`MAV_AUTOPILOT_ARDUPILOTMEGA = 3`).
- Vehicle system IDs: INS-01 = 1, DEL-01 = 2, DEL-02 = 3 (from
  `config/fleet.json`).
- No raw-frame endpoint exists. The renderer cannot construct a MAVLink packet.

---

## 1. Link topology

`telemetry.topology` is either `shared` or `independent`. Both are implemented;
neither is verified on hardware.

**Shared multipoint (`topology: "shared"`)**

One ground modem carries all three vehicles. This is valid only if the RFD868x
configuration genuinely supports multipoint with unique airborne system IDs, and
that must be proven during commissioning. The GCS opens a single transport and
routes messages by source system ID. If a heartbeat arrives from an unknown
system ID it is ignored and an `UNEXPECTED_SYSID` interlock event is logged (rate
limited to once per 10 s per ID).

**Independent point-to-point (`topology: "independent"`)**

Three separate ground modems / links, one per aircraft. Each transport is bound to
a callsign, and messages on that link are still validated against the expected
system ID. This is the topology that avoids relying on unverified multipoint
behaviour.

In both cases:

- Detection of a COM port is **not** a connection. `system/devices` reports
  `is_modem_candidate` purely from USB VID/PID/description and explicitly does not
  claim a modem or a paired aircraft.
- `MavlinkTransport.open()` waits for a real `HEARTBEAT` (3 s) before reporting
  connected. A port that opens but never yields a heartbeat fails.
- The reader thread supervises the link and reopens after errors; `connected`
  drops when heartbeats age past `heartbeat_timeout_s` (3 s), and data is marked
  stale after `stale_after_s` (2 s).

Radio firmware version, hardware revision and region are `null` / `unverified` in
the shipped configuration. They must be filled from the actual units.

---

## 2. Identity validation

A vehicle is usable only when its heartbeat proves identity:

| Check | Requirement |
|---|---|
| System ID | matches the configured aircraft |
| Autopilot | `HEARTBEAT.autopilot == 3` (ArduPilot) |
| Vehicle type | `2` (quadrotor) for INS-01, `13` (hexarotor) for DEL-01/02; `0` accepted as unknown-generic |
| Armed flag | decoded from `base_mode & 128` |
| Flight mode | custom mode mapped through the ArduPilot Copter table |

A mismatch sets `identity_valid = false`, raises `IDENTITY_MISMATCH`, and blocks
readiness. Heartbeats from unknown system IDs never create a vehicle entry.

---

## 3. Telemetry consumed

| Message | Fields used | GCS meaning |
|---|---|---|
| `HEARTBEAT` | `type`, `autopilot`, `base_mode`, `custom_mode` | link, identity, armed, mode |
| `GLOBAL_POSITION_INT` | `lat`, `lon`, `alt`, `relative_alt`, `vx`, `vy`, `vz`, `hdg` | position, relative altitude, groundspeed, vertical speed, heading |
| `GPS_RAW_INT` | `fix_type`, `satellites_visible`, `eph` | GPS quality |
| `SYS_STATUS` | `voltage_battery`, `battery_remaining` | pack voltage, percentage |
| `BATTERY_STATUS` | `battery_remaining`, `voltages` | percentage, cell-sum voltage |
| `EKF_STATUS_REPORT` | `flags`, `velocity_variance` | EKF health |
| `DISTANCE_SENSOR` | `distance` | rangefinder / AGL (TFmini-S on delivery aircraft) |
| `RADIO_STATUS` | `rssi` | link quality percentage (capped 0–100) |
| `MISSION_CURRENT` | `seq` | active waypoint index |
| `STATUSTEXT` | `severity`, `text` | raw autopilot text, preserved verbatim with `severity_raw`; reaches the event log, not the vehicle card (`FR-014`) |
| `COMMAND_ACK` | `command`, `result` | command transaction resolution |
| `TUNNEL` | JSON payload | project events from the companion computer |

`TUNNEL` payloads are UTF-8 JSON objects with a `type` or `event_type`. They carry
survivor reports, payload/break-beam results and AI metadata. A malformed tunnel
payload raises `EVENT_DECODE` and is discarded; it never crashes ingest.

Altitude note: `relative_alt` is relative to the autopilot's home altitude. The
AGL used for 12 m search altitude and 3 m release clearance comes from the
rangefinder when available and is otherwise derived; the GCS labels which source
was used and refuses a release without a valid clearance measurement.

---

## 4. Commands sent

All commands go through `TelemetryManager.send_command()` and become a
`CommandTransaction` with a `tx_id`. The transaction is `SENT`, then
`ACKNOWLEDGED` (`MAV_RESULT 0`) or `REJECTED` (any other result), or `TIMEOUT`
after `command_timeout_s` (2.5 s). Only `ACKNOWLEDGED` advances a safety-critical
state machine; a timeout never counts as success.

| Purpose | MAV_CMD | Params | Notes |
|---|---|---|---|
| Arm / disarm | `MAV_CMD_COMPONENT_ARM_DISARM` (400) | `param1` 1 or 0 | only after gate/pad checks |
| Guided takeoff | `MAV_CMD_NAV_TAKEOFF` (22) | altitude | INS-01 launch, delivery launch |
| Set mode | `MAV_CMD_DO_SET_MODE` (176) | `MAV_MODE_FLAG_CUSTOM_MODE_ENABLED`, mode | e.g. LOITER (5) on abort |
| Return to launch | `MAV_CMD_NAV_RETURN_TO_LAUNCH` (20) | – | controlled abort fallback |
| Reposition | `MAV_CMD_DO_REPOSITION` (192) | lat/lon/alt/speed | defined for delivery task manoeuvres |
| Land | `MAV_CMD_NAV_LAND` (21) | – | defined; recall uses ArduPilot landing |
| Payload latch | `MAV_CMD_DO_SET_SERVO` (183) | servo, PWM | ONE servo per kit, only from the ledger state machine |

Mode numbers follow the ArduPilot Copter table (0 STABILIZE … 3 AUTO, 4 GUIDED,
5 LOITER, 6 RTL, 9 LAND, 21 SMART_RTL, 27 AUTO_RTL). Commands are addressed to
component 1. Each command is sent once and tracked; there is no silent retry
loop. A missing ACK becomes `TIMEOUT` and is surfaced to the operator/state
machine. (`command_retries` exists in the configuration but is currently
unused; this is listed in `KNOWN_ISSUES.md`.) A transaction is never silently
upgraded to success.

### Payload release path

Release is not a UI button that sends a servo command. It is the terminal step of
the delivery state machine:

1. Allocation reserved exactly one kit in the ledger (atomic, no double reserve).
2. Aircraft reaches `HOVER_VERIFY` and the safety engine confirms stable hover,
   altitude clearance, valid position and no active recall.
3. Ledger transitions the kit to `RELEASE_SENT` and only then emits
   `MAV_CMD_DO_SET_SERVO`.
4. Break-beam evidence resolves the kit: `CONFIRMED` → `DELIVERED`;
   `NO_BEAM`/`SENSOR_FAULT`/`UNKNOWN` → `UNKNOWN`. **A missing beam never triggers
   an automatic retry.**
5. Duplicate release is blocked by ledger state; the second attempt raises.

The GCS cannot arm, take off, descend or release from the copilot or from an
unvalidated renderer message.

---

## 5. Mission upload (REAL)

Sequence per aircraft, for the search mission and then the inclusion fence:

```
GCS  → MISSION_COUNT(count, mission_type)
FC   → MISSION_REQUEST_INT(seq)          (or MISSION_REQUEST)
GCS  → MISSION_ITEM_INT(seq, frame, command, current, autocontinue, p1..p4, x, y, z)
… repeated for each item …
FC   → MISSION_ACK(type)
```

Then upload is verified by read-back:

```
GCS  → MISSION_REQUEST_LIST(mission_type)
FC   → MISSION_COUNT(n)
GCS  → MISSION_REQUEST_INT(0..n-1)
FC   → MISSION_ITEM_INT(…)
```

A stream is `VERIFIED` only when the read-back item count matches and the
protocol completes; an acknowledgement without a matching read-back is
`UNVERIFIED`. Timeouts default to 20 s per stream.

- Mission items: `MAV_FRAME_GLOBAL_RELATIVE_ALT_INT` (6), commands
  `NAV_TAKEOFF` (22), `NAV_WAYPOINT` (16) per lane vertex, `NAV_RETURN_TO_LAUNCH`
  (20). Coordinates are sent as integer degrees × 1e7.
- Fence items: `MAV_MISSION_TYPE_FENCE` (1), `MAV_FRAME_GLOBAL_INT` (5),
  `FENCE_POLYGON_INCLUSION` (5001) per unique vertex with `param1 = vertex count`,
  then `FENCE_RETURN_POINT` (5002). A closed KML ring is de-duplicated so the
  first vertex is not sent twice.
- Delivery aircraft have no search mission; their mission stream is
  `NOT_REQUIRED`, but the fence and parameters are still uploaded.
- In REAL mode any receipt marked `simulated` forces
  `upload_verified = false`, so a simulator receipt can never open the gate.

---

## 6. Parameters written during upload

Only these documented ArduPilot parameters are written, then read back and
compared:

| Parameter | Value | Reason |
|---|---|---|
| `FENCE_ENABLE` | 1 | enable geofence |
| `FENCE_TYPE` | **5** — altitude (1) + inclusion polygon (4) | circle/map (bit 2) is **deliberately not enabled** |
| `FENCE_ACTION` | 1 | RTL on breach |
| `FENCE_ALT_MAX` | plan `geofences[callsign].max_alt_m` | ceiling |
| `RTL_ALT` | profile RTL altitude × 100 (cm) | consistent return height |
| `FS_THR_ENABLE` | 1 | keep throttle failsafe enabled |
| `BATT_FS_LOW_ACT` | 2 | low battery action |
| `BATT_FS_CRT_ACT` | 1 | critical battery action |
| `WPNAV_SPEED` | search or delivery speed × 100 (cm/s) | planned speed; the key differs by role |

`FENCE_TYPE` is a bitmask, and the plan authors a polygon and a ceiling but **no circle
radius**. Enabling bit 2 would arm a breach monitor whose trigger distance is whatever
`FENCE_RADIUS` happens to hold on that airframe — a fence the operator did not plan. So circle
protection stays off until the plan carries a radius and it is proven on hardware. Guarded by
`test_fence_circle_is_never_enabled_without_a_radius`. `SIMULATED`, `PENDING TEST` on hardware.

**Parameters the station does not write, and why:**

| Parameter | Status |
|---|---|
| `FS_GCS_ENABLE` | **Not written.** The station transmits no `HEARTBEAT`, so enabling the GCS comm-loss failsafe would put the aircraft in RTL/land on a link the autopilot believes is dead. Sequence: heartbeat sender first, then this parameter, then a read-back gate in preflight. Finding `H-6` / `A-2`, requirement `FR-016`, fix row `27a` |
| `SYSID_MYGCS` | Not written — same reason; it names the GCS the failsafe watches |
| `FS_EKF_ACTION`, `FS_EKF_THRESH` | Not written — behaviour on an EKF failure is the airframe's configured default, and choosing it without bench data would be inventing hardware behaviour |
| `BATT_LOW_VOLT`, `BATT_CRT_VOLT` | Not written — voltage thresholds belong to a specific pack that has never been measured here. The GCS writes the *actions*, not the trigger levels |

Parameters are written with `PARAM_SET` and verified with `PARAM_REQUEST_READ` /
`PARAM_VALUE`, with a 1 % or 1e-3 tolerance. Failsafes are **enabled**, never
disabled. The GCS does not expose a route to turn off ArduPilot failsafes, and a
GCS crash leaves every onboard protection active.

---

## 7. Failsafe and disconnect behaviour

- Onboard ArduPilot failsafes remain authoritative. The GCS never sets
  `FS_THR_ENABLE = 0` or equivalent.
- **The GCS does not transmit a `HEARTBEAT`.** It parses inbound heartbeats for presence,
  age and mode, and answers vehicle requests, but as a MAVLink component it is silent.
  A station that goes missing is therefore invisible to `FS_GCS_ENABLE`, which is exactly why
  that parameter is left at 0 (see §6). This is the single most consequential gap in the
  communication layer: `FR-016`, finding `A-2`/`H-6`, fix row `27a`.
- If telemetry is lost, the GCS marks the link lost and raises an interlock; it
  does not attempt to "recover" by sending blind commands. Commands are single-shot —
  `attempts` stays 1 and there is no `RETRYING` state — so a recall packet lost on the
  radio is not re-sent. Deliberate for payload release, unresolved for recall
  (`H-7`, fix row `27b`).
- If the GCS process dies, vehicles continue under their uploaded mission and
  onboard failsafes. Nothing in the GCS is required to keep the aircraft safe.
- Emergency recall is an operator action that commands LOITER/RTL/land through
  acknowledged transactions; it is not a substitute for onboard failsafes.
- `STATUSTEXT` is preserved verbatim with its raw severity and published to the event
  stream; it is **not** parsed into control decisions, and it is not yet rendered on the
  aircraft it came from (`FR-014`, fix row `42`).

---

## 8. What is not claimed

- No RFD868x multipoint capability is claimed. Shared topology remains
  `topology_verified: false` until commissioning proves it.
- No pairing is inferred from USB enumeration.
- No MAVLink 1 fallback is advertised.
- No real upload, fence write, parameter write, arm, takeoff, release or landing
  has been executed against hardware in this repository. Those are
  **PENDING TEST**.
- The TX800 analog video path is entirely separate from MAVLink and carries no
  telemetry or AI metadata.
