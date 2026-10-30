# RescueSwarm GCS — SITL and Bench Guide

Status: **DESIGNED**, not executed in this workspace. ArduPilot SITL was not
installed here; only MAVProxy is present. The commands below are the intended
bench procedure. Do not mark SITL results as flight evidence.

SITL exercises the REAL-mode code path (MAVLink transport, identity checks,
mission/fence/parameter upload protocol, command ACKs) against a simulated
autopilot. It does not validate radios, cameras, servos or the break-beam.

---

## 1. What you need

- ArduPilot source with SITL built (Linux/WSL recommended; Mission Planner's
  built-in SITL is acceptable on Windows).
- MAVProxy (`mavproxy.py` is installed in the tested environment).
- This GCS backend and UI.
- Optional: `--map` and `--console` for visual checks.

The GCS needs no internet. SITL home must be set to the mission pad so the
imported boundary is reachable:
`12.9487, 77.5991` (from `config/fleet.json`).

---

## 2. Start three SITL vehicles

Use distinct system IDs and distinct output ports. Run each in its own terminal
(or use `screen`):

```bash
# INS-01 — quad, sysid 1
sim_vehicle.py -v ArduCopter --frame=quad --sysid=1 \
  --home=12.9487,77.5991,0,0 \
  --out=udp:127.0.0.1:14550 --no-rebuild

# DEL-01 — hexa, sysid 2
sim_vehicle.py -v ArduCopter --frame=hexa --sysid=2 \
  --home=12.9487,77.5991,0,0 \
  --out=udp:127.0.0.1:14551 --no-rebuild

# DEL-02 — hexa, sysid 3
sim_vehicle.py -v ArduCopter --frame=hexa --sysid=3 \
  --home=12.9487,77.5991,0,0 \
  --out=udp:127.0.0.1:14552 --no-rebuild
```

Adjust ports if they are busy. Each instance must report a heartbeat and the
expected `sysid`. In MAVProxy you can confirm with `status` and `watch
HEARTBEAT`.

If your SITL build does not understand `--frame=hexa`, use Mission Planner's
frame selector or set `FRAME_CLASS`/`FRAME_TYPE` parameters; the GCS only checks
the MAVLink vehicle type (quadrotor 2 for INS-01, hexarotor 13 for DEL-01/02). A
SITL that reports generic type 0 is accepted, but identity validation is weaker
and is logged as such.

---

## 3. Point the GCS at SITL

The shipped config has no endpoints. Use the configuration API (or edit
`config/fleet.json` before start; runtime overrides are stored in
`data/settings.json`).

```bat
curl -X PATCH http://127.0.0.1:8765/api/system/config ^
  -H "Content-Type: application/json" ^
  -d "{\"patch\":{\"telemetry\":{\"topology\":\"independent\",\"baud\":57600,\"independent_endpoints\":{\"INS-01\":\"udpin:127.0.0.1:14550\",\"DEL-01\":\"udpin:127.0.0.1:14551\",\"DEL-02\":\"udpin:127.0.0.1:14552\"}}}}"
```

Notes:

- `udpin:` tells pymavlink to bind and receive; SITL is sending to that port.
- Do not set `topology: "shared"` unless you have actually proven multipoint.
  With three `--out` endpoints, independent is the truthful choice.
- Leave `topology_verified: false` unless the bench link was explicitly proven.
  Setting it does not fabricate a connection; heartbeats do.
- For a single shared UDP fan-in bench, you may instead use `shared` with
  `shared_endpoint: "udpin:127.0.0.1:14550"` and forward all three SITL outputs
  to that port. This tests the shared code path but not RF multipoint hardware.

Video readiness blocks REAL missions when feeds are unmapped. For a telemetry
and upload bench you may temporarily set:

```json
{"patch":{"video":{"required_for_readiness":false}}}
```

This is a **bench-only downgrade**. Restore `true` before any real mission. The
UI and preflight still state that video is unmapped; disabling the requirement
does not mark a feed live.

Restart the backend after changing transport settings if the connect call keeps
using old endpoints (mode switching reopens transports, but a restart is the
simplest reset).

---

## 4. Bench sequence

1. Switch the station to **REAL** (`POST /api/system/mode {"mode":"REAL"}` or the
   UI toggle). The UI must show REAL everywhere.
2. Connect fleet. Expect three identities:
   `autopilot=3`, types quad/hexa, system IDs 1/2/3.
3. Confirm `GET /api/fleet` shows fresh telemetry and `identity_valid: true`.
4. Import the sample KML and generate a plan.
5. Sign manual items; run preflight. Inspect `GET /api/mission/preflight` for
   blockers. Video will block unless the bench downgrade above is applied.
6. Upload. Watch MAVProxy for `MISSION_COUNT`, `MISSION_REQUEST_INT`,
   `MISSION_ITEM_INT`, `MISSION_ACK`, then `MISSION_REQUEST_LIST` read-back.
7. Confirm receipts are `VERIFIED`, `simulated: false`, and the gate opens.
8. Only if the bench is safe, type `START` and observe AUTO. SITL has no props;
   still treat arm/takeoff as a real command path.
9. Test ABORT: expect LOITER and an acknowledged `DO_SET_MODE`.
10. Test EMERGENCY RECALL: expect RTL/land behaviour and a recorded outcome.
11. Kill the GCS backend while SITL is flying. The vehicle must continue under
    its onboard mission/failsafes; the GCS must not have modified failsafe
    parameters to depend on itself.

---

## 5. Expected MAVLink evidence to capture

- Heartbeat identity for each sysid.
- Mission upload request/ack sequence and read-back item count.
- Fence upload (`mission_type=1`) and parameter write/read-back.
- `COMMAND_ACK` for arm/takeoff/mode commands.
- Timeout behaviour when SITL is paused or a link is cut.

Save MAVProxy logs and the GCS event export with the test report. Label the
result **SITL**, not flight.

---

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| No heartbeat | wrong port direction (`udpin` vs `udpout`), firewall, SITL not sending to that port |
| Unexpected sysid | `--sysid` not applied, or another SITL instance on the same port |
| Identity mismatch | SITL vehicle type is not quad/hexa, or autopilot is not ArduPilot |
| Upload times out | SITL not accepting `MISSION_COUNT`; check mission type support for fence |
| Parameter read-back fails | SITL parameter name mismatch; inspect MAVProxy `param show` |
| Gate still blocked | video requirement, manual sign-offs, upload, freshness, pad, kits or recall; read the blocker list |

---

## 7. After SITL

SITL does not clear a hardware item. The progression remains:

1. SITL (this guide) — protocol correctness.
2. Prop-off bench with real radios and avionics — link, identity, upload,
   parameters, servo direction (no release).
3. Tethered / restrained test — arm, takeoff, hover, release mechanism with a
   dummy load and beam evidence.
4. Supervised flight test — full mission with safety pilot and manual override.

Each stage must be recorded with its own evidence label. The GCS keeps ArduPilot
failsafes enabled throughout, and no stage may be skipped because simulation
passed.
