# Survivor Detection, Geotagging and Accuracy — 30 marks

Team ADRODS-X · NIDAR PS1 · Phase 4A design review · Rubric row 5

## Key message

INS-01 finds survivors with a deliberately small YOLOv8n model on a Hailo-8, confirms each one over at least 3 frames, and turns the box centre into latitude and longitude with a simple nadir pinhole formula that works because the gimbal keeps the camera level; GNSS error dominates the budget, so we target a median error of 2 m or less and a 95th percentile of 3 m or less and will prove it on surveyed dummies before claiming it.

---

## Slide plan

### Slide 1: How INS-01 finds survivors

- INS-01 flies 9 lawnmower lanes at 12 m AGL and 8 m/s: 10 ha in 10.15 min.
- Pi Camera 3 Wide (12 MP, 102 deg horizontal FOV) on a 2-axis gimbal held pointing straight down.
- YOLOv8n on the Hailo-8 (26 TOPS), two 640 x 640 tiles per 1280-px-wide frame: a 0.5 m wide person is about 21.6 px.
- Motion blur 0.8 cm (0.35 px) at 1/1000 s exposure; each point on the ground stays in view about 2 s.
- A survivor is confirmed after at least 3 frames with mean confidence of 85% or more; sightings within 5 m are one survivor.
- Fallback if measured recall at 12 m is low: 10 m AGL (25.9 px, 12.34 min search).

Suggested visual: pipeline strip (camera -> tiles -> YOLOv8n -> 3-frame confirm -> geotag -> GCS) above a sketch of the 29.6 m x 15.9 m footprint on a lane.

### Slide 2: From a pixel to latitude and longitude

- Gimbal keeps the camera level, so ground offset = pixel offset x height / focal length.
- Height H = barometric altitude above the launch point (flat field); the TFmini-S is not used on INS-01 because it is reliable only to about 7 m on dark ground.
- Rotate the offset by the drone heading to East/North and add it to the drone GNSS position at the frame time.
- Worked example: pixel (900, 250), H = 12 m, heading 30 deg -> 6.49 m East, 0.80 m South of the drone.
- The reported position is the running mean of the confirming frames.

Suggested visual: image frame with the pixel offset arrow, then the same arrow on a North-up map rotated by the heading.

### Slide 3: Error budget and how we will prove accuracy

- M10 GNSS (about 2 m CEP class) is the dominant term and does not average out over 3 frames.
- Our own camera-side terms (examples): 1 deg tilt 0.21 m; 0.05 s time skew 0.40 m; 0.5 m height error up to 0.62 m at the image edge.
- Illustration only: camera-side terms combine to about 0.5 m near the image centre and about 0.9 m at the edge.
- Internal targets: median 2 m or less, 95th percentile 3 m or less. Status: PENDING ground-truth tests with surveyed dummies.
- Test plan: camera calibration, bidirectional passes over surveyed dummies, recall and false-alarm counts, duplicate tests.

Suggested visual: horizontal bar chart of the error terms (image centre vs image edge), with the GNSS bar in a different colour and the 2 m / 3 m targets as vertical lines.

---

## 1. Detection chain on INS-01

```text
 Pi Camera 3 Wide (nadir, 2-axis gimbal)
      | frame + sensor timestamp
      v
 1280 x 640 central band -> two 640 x 640 tiles (left, right)
      |
      v
 YOLOv8n on Hailo-8 (single class: person) -> boxes + confidence
      |
      v
 box centre -> lens undistortion -> pixel (u, v)
      |                                   Cube Orange+ (MAVLink): GNSS position,
      v                                   heading, baro height, interpolated to
 geotag (nadir pinhole formula) <-------- the frame timestamp
      |
      v
 track across frames: >= 3 frames, mean confidence >= 0.85 -> CONFIRMED
      |  position = running mean of the confirming frames
      v
 5 m merge (also discard geotags outside the KML zone)
      |
      v
 survivor report -> Cube serial -> RFD868x -> GCS (ID, lat, lon, confidence, frames, time)
```

| Item | Choice | Reason |
|---|---|---|
| Camera | Raspberry Pi Camera Module 3 Wide, 12 MP, 102 deg HFOV | Wide swath per lane; native Pi 5 support |
| Mount | 2-axis gimbal (roll, pitch) held nadir | Keeps the geotag maths simple and accurate |
| Computer | Raspberry Pi 5 + Hailo-8 AI accelerator (26 TOPS) | On-board inference; no video link needed for AI |
| Model | YOLOv8n (about 3.2 M parameters), single class "person" | Small, fast, easy to compile for Hailo; enough for one class |
| Input | Two 640 x 640 tiles per 1280-px-wide frame | Keeps full resolution: 21.6 px per 0.5 m at 12 m instead of 10.8 px with one 640 px input |
| Confirmation | At least 3 frames, mean confidence 0.85 or more | Rejects one-frame false alarms; a false survivor would waste a kit |
| Duplicate merge | Within 5 m = same survivor (INS-01 and GCS) | One survivor ID, one kit |
| Live video | RunCam Nano 2 + TX800 analog to the GCS | Operator view only; AI detections travel over telemetry |

Tiling plan: the central 1280 x 640 band of each 1280 x 720 frame is cut into two exact 640 x 640 tiles, so nothing is resized. Cropping 80 rows along-track does not reduce coverage, because every ground point still passes through the band (about 1.85 s in view instead of 2.0 s).

---

## 2. Search geometry and pixel budget

Focal length at 1280 px width: fx = 1280 / (2 x tan 51 deg) = 518.3 px. Person width 0.5 m. Usable swath = 80% of the optical footprint (ASSUMED).

| AGL | Speed | Optical footprint across | Usable swath | Lanes | Search time | Ground sample | Person (0.5 m) | Blur at 1/1000 s |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 m (fallback) | 8 m/s | 24.7 m | 19.76 m | 11 | 12.34 min | 1.93 cm/px | 25.9 px | 0.8 cm (0.41 px) |
| **12 m (baseline)** | **8 m/s** | **29.6 m** | **23.71 m** | **9** | **10.15 min** | **2.32 cm/px** | **21.6 px** | **0.8 cm (0.35 px)** |
| 15 m (rejected) | 8 m/s | 37.1 m | 29.64 m | 7 | 7.95 min | 2.89 cm/px | 17.3 px | 0.8 cm (0.28 px) |

Other useful numbers at 12 m (CALCULATED):

| Quantity | Value |
|---|---|
| Lane spacing (200 m / 9) | 22.2 m |
| Overlap between neighbouring lanes (29.6 - 22.2) | about 7.4 m |
| Along-track footprint (67 deg VFOV) | about 15.9 m -> about 2 s in view at 8 m/s |
| Image edge distance from the track | 14.8 m |
| Lying adult, 1.7 m long (ASSUMED size) | about 73 px long x 22 px wide |
| Blur at 1/250 s (if light is poor) | 3.2 cm (1.4 px) |

Why 12 m and 8 m/s:

- 12 m gives 9 lanes and a 10.15 min search, leaving time for delivery inside the 30-min cap.
- About 22 px across the narrow side of a person is near the lower end of what small-object detectors handle well, which is why we fine-tune on aerial data and keep a 10 m fallback.
- 12 m stays well clear of low obstacles and below the 18 m and 22 m delivery layers.
- 8 m/s with a 1/1000 s exposure keeps blur below half a pixel.

Frame-rate need: a person is in view for about 2 s. Three confirming frames need only about 1.5 frames/s in theory. Planning target: 10 frames/s (20 tiles/s) so each survivor gets many looks. PENDING benchmark on the Pi 5 + Hailo-8.

---

## 3. The detector: small model, aerial training

### 3.1 Why YOLOv8n

- One class (person) on a plain field does not need a large model.
- The small model compiles cleanly for the Hailo-8 and leaves throughput for two tiles per frame at a useful frame rate.
- The 3-frame confirmation gives more benefit against false alarms than a bigger model would, at no extra cost.
- Lower power and heat on a drone that also runs the camera, gimbal and telemetry.

### 3.2 Training plan

| Step | Plan |
|---|---|
| Start point | Pretrained YOLOv8n weights |
| Public aerial data | Aerial person datasets such as HERIDAL, SARD and VisDrone, rescaled so people appear about 20-25 px wide (our 2.3 cm/px) |
| Own data | Dummy images from 10-12 m AGL over grass, soil and rubble; lying and sitting poses; several clothing colours; morning and midday light |
| Negatives | Bags, logs, tarps, shadows, tyres: images with no person |
| Augmentation | Any rotation (lanes run both ways), flips, brightness and contrast, up to 1 px blur, scale +/- 20% |
| Validation | Held-out own flights only, never used in training |
| Deployment | Export to ONNX, compile to an INT8 model with the Hailo Dataflow Compiler using our own images for calibration; compare recall before and after quantisation |

### 3.3 Confirmation, merge and filtering

| Rule | Value | Purpose |
|---|---|---|
| Per-frame candidate | Person box above a candidate threshold (tuned on test data) | Start a track |
| Track association | Geotags of consecutive boxes within the 5 m gate | Same person across frames |
| Confirmation | At least 3 frames and mean confidence 0.85 or more | Only real survivors trigger a kit |
| Reported position | Running mean of the confirming frames' geotags | Averages pixel and attitude noise |
| Merge | Within 5 m of a known survivor -> update it, no new ID | Lane overlap and repeat sightings |
| Zone filter | Discard geotags outside the KML polygon | Crew and judges at the field edge |

---

## 4. Geotag method

### 4.1 Formula (camera level, image top = drone nose)

```text
du = u - cx                 dv = v - cy           (undistorted pixel offset from image centre)
right   =  du * H / fx                            (metres to the drone's right)
forward = -dv * H / fy                            (metres ahead of the drone)

East  = forward * sin(psi) + right * cos(psi)     (psi = heading, clockwise from North)
North = forward * cos(psi) - right * sin(psi)

lat_s = lat_d + North / 111,195                   (degrees; 111,195 m per degree)
lon_s = lon_d + East  / (111,195 * cos(lat_d))
```

| Symbol | Source |
|---|---|
| u, v | Box centre, corrected for lens distortion (one-time checkerboard calibration) |
| cx, cy, fx, fy | Camera calibration; nominal fx = fy = 518.3 px at 1280 px width, centre (640, 360) |
| H | Barometric altitude above the launch point, zeroed on the pad (flat field) |
| psi | INS-01 heading from the Cube Orange+ EKF |
| lat_d, lon_d | INS-01 M10 GNSS position at the frame time (pose interpolated to the sensor timestamp) |

The flat-earth conversion error is negligible over offsets of tens of metres.

### 4.2 Worked example

| Step | Calculation | Result |
|---|---|---|
| Inputs | H = 12.0 m, fx = fy = 518.3 px, centre (640, 360), box centre (900, 250), heading 30 deg | - |
| Pixel offset | du = 900 - 640, dv = 250 - 360 | du = 260 px, dv = -110 px |
| Metres per pixel | 12.0 / 518.3 | 0.02315 m/px |
| Right / forward | 260 x 0.02315; 110 x 0.02315 | right 6.02 m, forward 2.55 m |
| East | 2.55 x sin 30 + 6.02 x cos 30 = 1.27 + 5.21 | 6.49 m |
| North | 2.55 x cos 30 - 6.02 x sin 30 = 2.21 - 3.01 | -0.80 m (South) |
| Check | sqrt(6.02^2 + 2.55^2) = sqrt(6.49^2 + 0.80^2) | 6.54 m both ways |
| Lat/lon (example drone position 20.0000000 N, 78.0000000 E) | 20 - 0.80 / 111,195; 78 + 6.49 / (111,195 x 0.9397) | 19.9999928 N, 78.0000621 E |

The same survivor is seen in at least 3 frames; the reported position is the mean of the 3 or more geotags.

### 4.3 Why height comes from the barometer

- The TFmini-S is reliable only to about 7 m on dark ground, so it is not used on INS-01 at 12 m.
- On a flat field, barometric height above the launch point equals height above the survivor.
- A 0.5 m height error changes the ground offset by 4.2% (0.5 / 12): 0.21 m at 5 m from the centre, 0.62 m at the image edge (14.8 m).
- The barometer is zeroed on the pad just before START, which limits drift over a 12 min flight.

---

## 5. Error budget (examples, not measured)

| # | Source | Example input | Effect at 12 m | Behaviour | Mitigation |
|---:|---|---|---|---|---|
| 1 | INS-01 GNSS position | M10, about 2 m CEP class | about 2 m (dominant) | Slowly varying bias; 3-frame averaging does not remove it | Open sky, full GNSS warm-up before START, satellite count and HDOP in pre-flight checks |
| 2 | Gimbal / attitude not exactly nadir | 1 deg | 12 x tan 1 deg = 0.21 m | Bias plus noise | Gimbal calibration, level check on the pad |
| 3 | Camera-to-GNSS time skew | 0.05 s at 8 m/s | 0.40 m along-track | Sign flips with flight direction | Sensor timestamps; pose interpolated to exposure time; bidirectional test |
| 4 | Height error | 0.5 m | 0.21 m at 5 m offset, 0.62 m at the edge | Grows with distance from the image centre | Baro zeroed on the pad; flat field |
| 5 | Heading / camera yaw mounting (additional example) | 2 deg | 0.35 m at 10 m offset, 0 at the centre | Grows with distance from the centre | Compass calibration; measure the camera yaw offset |
| 6 | Box centre / pixel noise (additional example) | +/- 5 px | +/- 0.12 m | Random; averaging helps | Mean of 3 or more frames |
| 7 | Uncorrected lens distortion | 102 deg wide lens | Metres near the edges if ignored | Systematic | Checkerboard calibration; undistort before projection |
| 8 | Standing person (additional example) | Box centre about 0.85 m above ground | About 7% of the offset (about 1 m at the edge) | Pushes the geotag outward | Prefer frames near the image centre; confirm dummy pose in the rules |

Illustrative combination of camera-side terms 2-6 (root-sum-square of the examples, not a measured accuracy):

| Survivor position in the image | Terms included | Combined |
|---|---|---|
| Near the centre | 0.21, 0.40, 0, 0, 0.12 m | about 0.47 m |
| At the edge (14.8 m) | 0.21, 0.40, 0.62, 0.52, 0.12 m | about 0.93 m |

What this tells us:

- GNSS (about 2 m class) is the largest term. Meeting a 2 m median needs the M10 to perform at or better than its class on the day, which is why we measure instead of claim.
- The terms we control add about 0.5-0.9 m in these examples, and most of them shrink for survivors seen near the image centre.
- Averaging 3 or more frames reduces random terms (about 1/sqrt(3) = 0.58 for 3 frames) but not biases (GNSS, height, heading, time skew within one pass).
- The DEL drones navigate with the same type of GNSS at nearly the same time, so part of the GNSS error is common to both. How much cancels must be measured.

---

## 6. Accuracy targets and verification plan

Internal targets (our own, not rulebook numbers):

| Metric | Target | Status |
|---|---|---|
| Geotag median horizontal error | 2 m or less | PENDING TEST |
| Geotag 95th-percentile error | 3 m or less | PENDING TEST |
| Confirmed-survivor recall at 12 m | Threshold to be fixed before testing; below it we switch to 10 m | PENDING TEST |
| False survivors per full search | Target zero; every one is reported | PENDING TEST |

| Test | Method | Output | Status |
|---|---|---|---|
| T1 Camera calibration | Checkerboard images, OpenCV | fx, fy, cx, cy, distortion, reprojection error | PENDING |
| T2 Static geotag | INS-01 hovering at 12 m over markers at known offsets from the centre to the edge | Error vs image position; checks height and heading terms | PENDING |
| T3 Search-pass accuracy | 12 m, 8 m/s, several passes in both directions over at least 5 surveyed dummies | Median, 95th percentile, bias; time skew from the direction-dependent along-track bias | PENDING |
| T4 Recall and false alarms | Full lanes over dummies and decoys (bags, logs, tarps) | Confirmed recall, false survivors, recall vs image position and tile seam | PENDING |
| T5 Duplicates | Dummy in the lane overlap; two dummies 6 m apart | One ID per dummy, no false merge | PENDING |
| T6 Throughput and heat | Pi 5 + Hailo-8 running the full pipeline for 15 min | Frames per second, latency, temperature, no throttling | PENDING |

Ground truth: dummy positions are surveyed with a method better than the M10 (for example a borrowed RTK receiver, or tape measurements from a surveyed reference point), never a phone GPS fix. Absolute error and error relative to a reference marker in the same flight are reported separately.

---

## 7. What the GCS shows for each survivor

| Field | Example |
|---|---|
| Survivor ID | S4 |
| Latitude, longitude | 19.9999928 N, 78.0000621 E (7 decimals) |
| Mean confidence, frames used | 0.91, 5 frames |
| Time confirmed | mission clock 2:15 |
| Status | CONFIRMED -> ASSIGNED (DEL-01) -> DELIVERED / UNKNOWN / UNSERVED |
| Map | Numbered marker; coverage of flown lanes; lanes flown without AI shown as unsearched |

---

## Evidence status

| Item | Status | Note |
|---|---|---|
| Search setting 12 m / 8 m/s, 9 lanes, 10.15 min | CALCULATED | 80% usable swath and 3 s turns ASSUMED |
| Focal length 518.3 px, 21.6 px per person, 0.8 cm blur | CALCULATED | Nominal lens; replaced by calibration |
| Nadir pinhole geotag formula and worked example | CALCULATED | Formula DESIGNED; standard geometry |
| Error budget values | CALCULATED (examples) | Inputs are examples, not measurements |
| M10 GNSS about 2 m CEP | ASSUMED | Module class figure |
| YOLOv8n on Hailo-8, two tiles, 3-frame / 85% confirmation, 5 m merge | DESIGNED | Pipeline integration pending |
| Survivor list with 5 m merge on the GCS | BUILT-SIMULATED | Exists in our GCS software; tested in software simulation only |
| Fine-tuning on aerial datasets and own dummy images | PENDING TEST | Data collection not finished |
| Recall, false alarms, frame rate on Hailo-8 | PENDING TEST | No numbers claimed |
| Geotag median and 95th percentile | PENDING TEST | Targets 2 m / 3 m |
| Any flight with the AI running | PENDING TEST | Nothing is flight-tested |

---

## Risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| Low recall at about 22 px | Missed survivors | Aerial fine-tuning with rescaled data; own dummy images at 10-12 m; 10 m fallback (25.9 px) |
| False survivor confirmed | A kit and a delivery slot wasted | 3 frames with mean 0.85; negatives in training; zone filter; review logged frames |
| GNSS worse than expected on the day | Geotag error above 2 m | Warm-up and satellite checks before START; we report measured numbers, not hopes |
| Gimbal not level (calibration or vibration) | About 0.21 m per degree | Gimbal calibration; level check; vibration isolation |
| Camera-to-pose time skew | 0.40 m per 0.05 s at 8 m/s | Sensor timestamps; interpolation; bidirectional test measures it |
| Person lying across the tile seam | Two half boxes or a weaker detection | Join boxes cut at the seam; 5 m merge; test the seam case; add a small tile overlap if needed |
| Poor light forces long exposure | Blur | Cap exposure at 1/1000 s and raise gain; blur at 1/250 s is still 1.4 px |
| Two survivors closer than 5 m | Merged into one | Two boxes in the same frame stay two survivors (proposed); tested in T5 |
| Pi or AI crash in flight | Lanes flown blind | Pi watchdog restarts the AI; GCS marks those lanes unsearched |

---

## Likely judge questions

1. Can such a small model detect a person that is only about 22 px wide?
   That is the main risk, so we fine-tune on aerial person datasets and on our own dummy images at 10-12 m. We have not measured recall yet. If it is too low at 12 m we fly at 10 m (25.9 px), which costs about 2.2 min of search time.

2. How accurate is your geotag?
   We have not measured it yet. Our targets are a median of 2 m or less and a 95th percentile of 3 m or less, tested on surveyed dummies. The budget shows GNSS (about 2 m class) dominates; our camera-side terms add about 0.5-0.9 m in the examples.

3. Why do you ignore the drone's tilt?
   We do not ignore it; the 2-axis gimbal removes it by keeping the camera pointing straight down. Any remaining gimbal error shows up as about 0.21 m per degree at 12 m, and the static test checks it.

4. Why not use the lidar for height?
   The TFmini-S is reliable only to about 7 m on dark ground. At 12 m we use barometric height above the launch point, which works on a flat field. A 0.5 m height error moves the geotag by 4.2% of the offset, at most about 0.6 m at the image edge.
