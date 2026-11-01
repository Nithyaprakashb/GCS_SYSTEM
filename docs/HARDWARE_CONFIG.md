# RescueSwarm GCS — Hardware Configuration

Evidence status: the aircraft descriptions below are **DESIGNED / ASSUMED** from
the team's stated build. The GCS software support is **BUILT** and tested only in
simulation. No hardware link, flight or release described here is
**PHYSICALLY TESTED** by this repository.

`config/fleet.json` is the single source of truth for numbers the software uses.
Where the build is not yet fixed (radio topology, video device mapping), the
configuration deliberately contains `null` or `unverified` rather than a guess.

---

## 1. Fleet

| | INS-01 | DEL-01 | DEL-02 |
|---|---|---|---|
| Role | SEARCH | DELIVERY | DELIVERY |
| Frame | 650 mm X4 | 900 mm X6 | 900 mm X6 |
| Flight controller | Cube Orange+ / ArduPilot | Cube Orange+ / ArduPilot | Cube Orange+ / ArduPilot |
| Companion | Raspberry Pi 5 + Hailo-8 | – | – |
| Camera | Pi Camera 3 Wide (AI) + RunCam Nano 2 (video) | RunCam Nano 2 | RunCam Nano 2 |
| GNSS | M10 | M10 | M10 |
| Telemetry radio | RFD868x | RFD868x | RFD868x |
| Video transmitter | TX800 | TX800 | TX800 |
| Ranging | – | TFmini-S | TFmini-S |
| Payload | – | 5 kits, MG90S latch, IR break-beam | 5 kits, MG90S latch, IR break-beam |
| MAVLink sysid | 1 | 2 | 3 |
| Search altitude | 12 m AGL | – | – |
| Search speed | 8 m/s | – | – |
| Transit altitude | 25 m | 18 m | 22 m |
| RTL altitude | 25 m | 18 m | 22 m |
| Delivery speed | – | 10 m/s | 10 m/s |
| Drop height | – | 3 m | 3 m |
| Battery low / critical | 30 % / 18 % | 28 % / 16 % | 28 % / 16 % |

HYB-01/HYB-02 (hybrid search-and-deliver aircraft) are **superseded** and are not
part of the active configuration. Any UI or plan that still references them is a
defect.

Mission constants: 10 ha search area, up to 10 survivors, 200 g kit, 30 min hard
cap, 14–15 min target, 25 s service time, 3 s turn time, 30 s takeoff allowance,
105 s recovery allowance, 3 m drop, 2 m boundary drop inset, 3 m lane inset,
80 % usable swath, 5 m survivor merge distance, 8 m proximity warning,
20 m horizontal / 4 m vertical descent clearance.

---

## 2. Telemetry radios (RFD868x)

The RFD868x is a transparent serial radio. Nothing about its pairing can be
inferred from a COM port. Two topologies are supported in software:

1. **Shared multipoint** — one ground radio reaches all three airborne radios.
   This requires the actual RFD units and firmware to support multipoint and
   unique system IDs. It is **not assumed**; `telemetry.topology_verified` is
   `false` and the GCS reports `Radio topology unverified — commissioning
   required`.
2. **Independent point-to-point** — three ground radios, one per aircraft, each
   bound to a single callsign. This is the conservative fallback.

Configuration fields:

```json
"telemetry": {
  "topology": "shared",
  "topology_verified": false,
  "gcs_sysid": 255,
  "baud": 57600,
  "shared_endpoint": null,
  "independent_endpoints": {"INS-01": null, "DEL-01": null, "DEL-02": null},
  "heartbeat_timeout_s": 3.0,
  "stale_after_s": 2.0,
  "command_timeout_s": 2.5,
  "command_retries": 2,
  "radio_firmware": null,
  "radio_hardware_revision": null,
  "radio_region": "IN — unverified"
}
```

To commission: record the firmware version and region of every radio, choose the
topology that was actually proven, set the endpoint(s) (e.g. `COM5`), and only
then set `topology_verified` to `true` with evidence in the commissioning log.
Changing the JSON alone does not prove a link; a heartbeat from the correct
system ID does.

Baud is 57 600 by default and must match the RFD serial setting.
`command_retries` is present in the configuration but not yet implemented;
commands are single-shot with a `TIMEOUT` result rather than silently retried.

**The station does not transmit a MAVLink `HEARTBEAT`.** It listens to the aircraft's
heartbeats and answers their requests, but as a ground component it stays silent, so the
autopilot cannot see it. Consequences for commissioning:

- `FS_GCS_ENABLE` must remain 0. Enabling it before a heartbeat sender exists makes
  link loss *more* dangerous, not less — the aircraft would react to a GCS it was never
  told about. `SYSID_MYGCS` is likewise not written. See §6 of `MAVLINK_MAPPING.md`.
- Link quality comes from `RADIO_STATUS.rssi` only. Packet loss, rx-available rate and
  round-trip latency are **not computed**, and the UI reports them as unavailable. If the
  modem exposes its own diagnostics, recording them is commissioning work this build does
  not do for you.
- Port enumeration proves a device is present. It never proves a pairing: the GCS does not
  assume any detected COM port is the RFD868x.

---

## 3. Analog video (TX800 / RunCam)

Three independent analog receivers feed three USB capture devices — one per
aircraft. The mapping is fixed and must be recorded; the GCS never auto-assigns a
capture device to an aircraft because a device name is not proof of which
receiver it is attached to.

```json
"video": {
  "feeds": [
    {"feed_id": "A", "callsign": "INS-01", "receiver": "RX-A",
     "device_fingerprint": null, "mapped": false},
    {"feed_id": "B", "callsign": "DEL-01", "receiver": "RX-B",
     "device_fingerprint": null, "mapped": false},
    {"feed_id": "C", "callsign": "DEL-02", "receiver": "RX-C",
     "device_fingerprint": null, "mapped": false}
  ],
  "sim_fps": 6,
  "freeze_timeout_s": 2.0,
  "black_luma_threshold": 12.0,
  "required_for_readiness": true
}
```

Rules enforced by the software:

- Each feed is recorded independently to its own file in the mission evidence
  folder.
- Health monitoring detects freeze (no new frame within the timeout), black
  (mean luma below threshold), signal loss and excessive latency. These are
  reported as warnings; video is required for readiness, so a missing feed
  blocks START.
- The Pi Camera 3 Wide is an **AI camera**, not the RunCam feed. YOLO results are
  not drawn on RunCam video because no validated frame synchronisation exists.
  Any bounding box shown elsewhere is labelled as an AI detection with its own
  confidence and timestamp, never as a video overlay.

REAL capture is implemented but **PENDING TEST** on the actual capture devices.
`device_fingerprint` and `mapped` must be filled during commissioning.

Two things commissioning has to do that this build cannot do for you:

- **Capture formats are not enumerated.** The reader accepts whatever resolution and frame
  rate the OS hands it; nothing queries the device's supported modes, and nothing warns you
  if the receiver presents a format the recorder will re-time. Requirement `FR-004`, fix row `44`.
- **The telemetry HUD overlay exists only on the synthetic feed.** Real analog frames pass
  through un-overlaid, because the station cannot timestamp-match a picture it does not
  generate; `latency_basis` reports `NOT_MEASURED` rather than guessing. Requirement `FR-073`,
  fix row `43`.

---

## 4. Payload mechanism

Each delivery aircraft carries five 200 g kits in a magazine. Each kit has:

- an MG90S latch servo driven by `MAV_CMD_DO_SET_SERVO`;
- an IR break-beam sensor that proves the kit left the aircraft;
- a unique kit ID (`DEL-01-K1` … `DEL-02-K5`).

The GCS reserves a kit before flight to the survivor, sends a single release
command at the hover-verify point, and waits for break-beam evidence. A
`CONFIRMED` beam makes the kit `DELIVERED`. Anything else makes it `UNKNOWN`.
There is no automatic retry and no software path to mark a kit delivered without
beam evidence. Physical servo channel, PWM values and beam polarity are
**PENDING TEST** and must be verified on the bench and then in a tethered test.

---

## 5. Ground control station

- Windows 10/11 x64 laptop, no internet required.
- Three USB serial adapters for the RFD links (or one shared multipoint link).
- Three USB video capture devices for the TX800 receivers.
- Local disk for evidence: SQLite database, per-mission folders, JSONL logs, video
  recordings and PDF reports.
- Local basemap, built before the station goes to the field. Place a Geofabrik regional
  `.osm.pbf` at `map/india-261030.osm.pbf`, then
  `python tools/offline_map.py extract` and `python tools/offline_map.py build --zooms 10-16`.
  Tiles land in `data/map_tiles/` with a manifest; `GET /api/map/manifest` reports which
  surface is on screen and the extract's data date. Without a pack the map still works, on
  the procedural reference surface, and says so.
- The backend binds only to `127.0.0.1`; the Electron window is the only client.

Recommended pre-mission check: run `GET /api/system/diagnostics` and confirm the
data directory is writable, ffmpeg is present, and the expected capture devices
are enumerated. Device enumeration is evidence of a device, not of a working
radio link or a correct receiver mapping.

---

## 6. Companion computer (INS-01)

Raspberry Pi 5 with Hailo-8 runs YOLOv8n on the Pi Camera 3 Wide and emits
survivor reports over a MAVLink `TUNNEL` message as JSON. The GCS re-validates
every report: valid WGS84 coordinates, inside the AOI, confidence ≥ 0.85, at
least three confirming frames, and plausible geotag uncertainty (≤ 50 m, else
reset to 3 m). Failing reports are rejected and logged with the reason.

The onboard three-frame confirmation is necessary but not sufficient; the GCS
does not trust the companion's decision blindly. The Pi's detections are not
synchronised to the RunCam video, so no overlay is produced.

---

## 7. Known configuration conflicts

| Item | Resolution |
|---|---|
| HYB-01 / HYB-02 hybrid aircraft | Superseded by INS-01 + DEL-01/02; not in config |
| One ground modem for three aircraft | Not assumed; shared topology must be proven, independent topology supported |
| Delivery transit altitudes 18 m vs 22 m | Kept per aircraft from the brief; planner uses each profile's value |
| Camera FOV / lane spacing | Planner computes 22.24 m spacing from 102° HFOV at 12 m with 80 % usable swath; this is CALCULATED, not flight-measured |
| AI camera vs RunCam | Separate sensors; no shared frame clock, therefore no YOLO overlay on analog video |
| Radio region | `IN — unverified`; regulatory configuration is a commissioning task |

All unresolved items are listed in `docs/KNOWN_ISSUES.md`.
