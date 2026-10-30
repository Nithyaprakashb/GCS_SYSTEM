# RescueSwarm GCS — Design System

**Status:** BUILT · SIMULATED (UI verified in headless Chrome and Electron)
**Inspiration:** professional flight-dispatch density (DJI FlightHub/Pilot), restrained
glass and typography (Apple iOS/Pro), at-a-glance fleet visibility (Tesla). No visual
element is copied; the layout was designed for a three-aircraft rescue sortie.

---

## 1. Design principles

1. **Operational truth over decoration.** Every panel answers a question an operator
   actually asks during a 15-minute sortie: where is each aircraft, what is it doing,
   which survivor is next, what is blocking a release.
2. **Evidence is visible.** REAL vs SIMULATION is labelled in the header, in every
   video panel and in the data-quality badges. Simulated feeds carry
   `TACTICAL OPTICAL FEED — NOT FLIGHT FOOTAGE`.
3. **Calm until it matters.** The default surface is quiet navy/graphite. Accent amber
   is reserved for authority actions and warnings; red is reserved for abort, recall
   and critical interlocks.
4. **Density with rhythm.** Dense mono readouts for values, generous names for
   aircraft and survivors, consistent 8 px spacing increments.
5. **One glance, one decision.** The mission header carries timer, fleet pips, link
   count, video count, abort and recall. Nothing safety-critical is buried in a menu.

---

## 2. Colour

Tokens live in `desktop/src/index.css` as CSS custom properties. Dark is the primary
theme; light is a sunlight-readable variant. Both are selected with
`document.documentElement.dataset.theme`.

### Dark (primary)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#06090f` | App canvas |
| `--surface` | `#0a111c` | Panels |
| `--surface-2` | `#0d1624` | Rows, inputs, secondary blocks |
| `--panel` | `rgba(11,18,30,.88)` | Glass panels over the map |
| `--line` | `rgba(148,177,211,.14)` | Hairline separators |
| `--ink` | `#e8eef8` | Primary text |
| `--ink-2` | `#b3c1d4` | Secondary text |
| `--muted` | `#788aa3` | Labels |
| `--faint` | `#4d5f78` | Metadata, coordinates |
| `--accent` | `#f0a03c` | Operator authority, search aircraft |
| `--signal` | `#5ad1c8` | Telemetry/link, live indicators |
| `--ok` | `#58c98b` | Delivered, pass, verified |
| `--warn` | `#e7b549` | Unknown, stale, degraded |
| `--danger` | `#ef5f63` | Abort, recall, critical |
| `--info` | `#65a8f5` | Assigned, in delivery, advisory |

### Light

Backgrounds `#e9edf1` → `#f8fafc`, ink `#0d1826`. Accent and status hues are darkened
for contrast on white (`--accent #b96a12`, `--signal #147f7a`, `--danger #c53a40`,
`--ok #1f8d5b`, `--warn #9a6a04`, `--info #2a6fc4`). The basemap switches to a light
procedural palette server-side; no CSS filter is used.

### Semantic rules

- **Amber** = operator authority (launch, abort confirm, search aircraft).
- **Teal** = link/telemetry health and live video.
- **Green** = confirmed outcome (delivered, verified, pass).
- **Amber-warn** = unknown/unconfirmed — explicitly not a success state.
- **Red** = destructive or critical (abort, recall, fail, lost).
- Every status also carries a word (`DELIVERED`, `UNKNOWN`); colour is never the only
  channel.

---

## 3. Typography

| Role | Face | Size / weight |
|---|---|---|
| UI | Manrope Variable | 14 px body, 15–20 px titles, 700–800 for headings |
| Numeric / IDs | JetBrains Mono Variable | 9–24 px; tabular figures |
| Micro labels | Manrope | 8–10 px uppercase, letter-spacing 0.08–0.14em |
| Mission timer | JetBrains Mono | 24 px, 600, tight tracking |

Both fonts are bundled via `@fontsource-variable`, so the station renders identically
with no network access.

---

## 4. Layout

- Application frame: 68 px icon rail · content column · 64 px top bar.
- Mission Overview: three-column grid — fleet rail (aircraft cards), centre tactical
  map and three-up video wall, right rail (survivors, allocation, AI advisory,
  warnings, autonomy timeline). Columns scroll independently; the map and video wall
  keep their own aspect.
- Panels use a 10 px radius, 1 px hairline, very restrained blur behind the map.
- Resizable inspector panels, map modal, video modal, and full-screen replay.

Breakpoints: the three-column mission grid collapses to a single scrolling column
below 1280 px; the setup grid collapses below 1100 px.

---

## 5. Components

| Component | Behaviour |
|---|---|
| `Panel` | Titled surface with icon, actions slot, dense body |
| `Badge` | Status chip with semantic tone and optional pulse |
| `Meter` | Battery/queue bar driven by value and tone |
| `Notice` | Inline advisory/warning/danger band |
| `Modal` | Focus-managed dialog; typed confirmation for destructive actions |
| `AircraftCard` | Callsign, role, battery, AGL/GS/task/kits/link/GPS, active delivery |
| `VideoPanel` | Feed, aircraft, receiver, health, latency, recording, expand |
| `TacticalMap` | MapLibre GL, offline raster tiles, AOI, lanes, geofences, vehicles, survivors |
| `SurvivorRow` | ID, status, owner, kit, ETA, coordinates |
| `AllocationRow` | Survivor → aircraft/kit, ETA, candidate count, lifecycle badge |
| `Timeline` | Decision events excluding raw telemetry floods |

---

## 6. Motion and feedback

- Transitions: `160ms cubic-bezier(.2,.7,.2,1)`; pulsing dots only for live/executing.
- Toasts confirm operator actions; confirmations never replace the audit log.
- Buttons that would be unsafe are disabled with a visible reason, not hidden.
- Loading uses a single spinner style; no fake progress bars.

---

## 7. Accessibility

- Semantic buttons and links; dialogs trap focus and close on `Escape`.
- Visible focus rings on inputs and buttons (`:focus-visible`).
- Status conveyed by text + colour, never colour alone.
- Minimum 24 px hit height for icon buttons; 30–36 px for primary controls.
- Coordinates and numeric values are monospaced for scanning and comparison.
- Light theme is not a filter; contrast is retuned per token.

---

## 8. Evidence presentation

- `REAL` / `SIMULATION` badge in the header and in each video panel.
- Data-quality chips on records (`SIMULATED`, `CALCULATED`, `LIVE`, `UNKNOWN`).
- Map badge: `LOCAL TILES · NO INTERNET`; legend states the basemap is procedural.
- Replay and reports state the mission mode and outcome from the database, not from UI
  assumptions.
