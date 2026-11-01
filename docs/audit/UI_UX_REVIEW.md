# RescueSwarm GCS — Independent UI/UX and Human-Factors Review
Reviewer role: Aerospace UI/UX Reviewer and Human Factors Engineer (independent pass).
Tree reviewed: `desktop/src`, plus backend files read only to check label and state truth, after
the recent MissionScreen / format.ts / index.css / TacticalMap fixes.
## 1. Scope note and method
**This is a static code-and-styles review. No pixels were captured.** Screenshot capture of the
running application was blocked in this environment, so nothing below is a report of observed
rendering. I did not launch the app, exercise any control, or run any test.
Read in full: `desktop/src/App.tsx`, `state/GcsContext.tsx`, `main.tsx`, `screens/BootScreen.tsx`,
`SetupScreen.tsx`, `MissionScreen.tsx`, `LogsScreen.tsx`, `ReplayScreen.tsx`, `SettingsScreen.tsx`,
`components/TacticalMap.tsx`, `VideoPanel.tsx`, `ui.tsx`, `lib/format.ts`, `lib/types.ts`,
`index.css` (all 2191 lines, the token block plus every rule cited), `docs/DESIGN_SYSTEM.md`,
`backend/rescueswarm/delivery.py`, and targeted reads of `telemetry.py`, `mission_engine.py`,
`system.py`, `events.py`.
Claims that rest on code reading rather than visual inspection are flagged inline. Specifically:
every WCAG ratio below is **computed from the literal hex tokens** in `index.css:3-80`, and the
rendered pair depends on the `color-mix()`/alpha cascade, so it is **not verifiable by static
review**. Likewise **not verifiable by static review**: whether any widget overlaps, clips or wraps,
whether a hit target reaches 24 px after the cascade resolves, and whether any panel is visually
balanced. Where the brief's own claims cannot be verified from source I say so instead of accepting
them.
One correction up front: **there is no 700 ms poll anywhere in the frontend.** The real refresh values
are listed in §12.
---
## 2. Boot screen (`screens/BootScreen.tsx`, `index.css:769-978`)
**Purpose.** Startup self-test and the single gate into the station. **Can:** read six mapped checks
with live detail strings, wait for the automatic 4 s re-check, enter. **Cannot:** act on anything,
including abort — this screen renders before the shell exists (`App.tsx:134-136`).
Design **8/10** — original radar/monogram composition, legible check list; the only screen that
ignores the theme system. Usability **7/10** — a single 44 px entry gate with the reason for
un-readiness in plain language, but no announcement as checks resolve. Hierarchy **8/10** — identity,
then evidence, then the one action; `fail` correctly outranks `warn`.
1. HIGH | `index.css:769-781` + `BootScreen.tsx:108,145,159` | Splash hard-coded dark (`#05080d`, `#e8eef8`) plus literal greys `#5f728b`, `#ef8f92`. | `DESIGN_SYSTEM.md:56-59` defines light as the **sunlight-readable** variant, so an outdoor operator gets a dark splash then a light station; `.splash-check .state` (`index.css:931-936`, `#5f728b` on `#05080d`) computes to about **4.1:1** for 10 px text, below AA. | Make `.splash` consume `--bg`/`--ink`/`--faint`.
2. HIGH | `BootScreen.tsx:68-70` | `ready` is true whenever no check is `fail`, so entry is allowed with five of six checks in `warn`, including "Not reported by this backend build" (`:65`). | Warns are visible but costless and nothing downstream re-presents them; no record that the operator accepted degraded startup evidence. | Read "Enter with N degraded checks" and log an action.
3. MEDIUM | `BootScreen.tsx:138` | For `state === "fail"` the detail span gets no tone class, so "failed" renders in neutral `.state` grey while only the label and X glyph are red. | The failure text should read as failure inside its own row; `index.css:931-939` has `.state.ok` and `.state.warn` but no `.state.fail`.
4. MEDIUM | `BootScreen.tsx:128-142` | No `aria-live` region. | Checks change silently; AT reads a static list of spinners. | `role="status"` + `aria-live="polite"` on `.splash-checks`.
5. LOW | `BootScreen.tsx:138` | Detail truncated at `maxWidth: 210` with ellipsis, full text hover-only via `title`. | The startup reason the operator most needs is unreachable by keyboard and invisible on a touchscreen. | Wrap to two lines.
6. LOW | `BootScreen.tsx:2,135` | `X` for fail and `TriangleAlert` for warn, while `SetupScreen.tsx:511` and `:929` use `ShieldAlert` for the same warn state. | Three glyphs, one semantic state; the recent `TriangleAlert` fix is locally correct but did not unify the vocabulary.
---
## 3. Mission setup (`screens/SetupScreen.tsx`, `index.css:980-1211`)
**Purpose.** Six-stage gated workflow: links, boundary, plan, inspection, preflight/upload, launch
authority. **Can:** choose SIMULATION or REAL, import a KML, generate and validate a plan, tick manual
sign-offs, run preflight, upload and read back, evaluate the gate, launch behind a typed START token.
**Cannot:** change any configured limit, or abort/recall **from this screen's body** — those exist only
in the top bar.
Design **8/10** — clear, original stage rail and headings. Usability **7/10** — good progressive
disclosure and disabled-with-reason behaviour, but the drop-zone is an ARIA violation and the START
field is unlabelled. Hierarchy **6/10** — the launch button is well placed, but the provenance
aggregate that should outrank it (`evidenceQuality`) is buried in a KV row of the last panel (`:1072`).
1. CRITICAL | `SetupScreen.tsx:638-642` | The Stage-2 "Mission profile" panel prints `12 m AGL`, `8 m/s`, `Survivor cap 10`, `Merge radius 5 m`, `Release clearance 20 m` as literal strings. | **Not** read from state or config, yet `SettingsScreen.tsx:260-263` shows the real `merge_distance_m`, `descent_clearance_m`, `time_cap_min` and `max_survivors`, and `types.ts:34` carries per-aircraft `search_alt_m`. The operator signs off launch against numbers that may contradict the Configuration screen — the brief's "ASSUMED presented as real" prohibition — and no row carries a provenance chip. | Bind to `state.mission.plan` / `api.config()` and label each.
2. HIGH | `SetupScreen.tsx:711,725` | "At 8 m/s ground speed" and "RTL after search · 25 m for INS-01 · 2 m boundary inset" are hard-coded prose on a panel whose header claims every number is "calculated, never hand-tuned" (`:688`). | The sentence contradicts itself: three numbers in that panel *are* hand-tuned JSX constants. | Bind to config or mark the sentence `DESIGNED`.
3. HIGH | `SetupScreen.tsx:111` | Stage-0 completeness requires `connectedCount === 3` and `video?.live_count === 3`, and `:247`, `:251`, `:255`, `:489`, `:495`, `:781` all print literal `/3` or `/10`. | The recent change moved the `/3` in App and MissionScreen onto state but **not here**: `state.video.total` (`types.ts:196`) and `state.survivors.max_survivors` (`types.ts:105`) exist and are ignored, so a station with a different fleet or cap shows a readiness gate that can never turn green. | Use `state.fleet.vehicles.length`, `state.video.total`, `max_survivors`, `state.kits.total`.
4. HIGH | `SetupScreen.tsx:582-625` | The drop-zone `div` carries `role="button"` and `tabIndex={0}` and *contains* a real `<button>` ("Use 10 ha sample zone", `:615`) plus a hidden file input. | Nested interactive content inside `role=button` is invalid; the composite control has no accessible name and `onKeyDown` (`:598`) handles Enter only, so Space does nothing. | Drop `role="button"`, keep the real buttons, add `aria-label` and Space.
5. HIGH | `SetupScreen.tsx:394-395` | `<label>Type START to confirm</label>` with no `htmlFor`, input with no `id`, `onChange` force-uppercases. | The most consequential typed gate in the station is not programmatically associated with its field, so AT announces an unlabelled edit box. `App.tsx:314` and `:325` already do this correctly, so the fix pattern exists in the codebase. | Add `id`/`htmlFor`.
6. MEDIUM | `SetupScreen.tsx:845` | "Run automated preflight" enables on `manual.every(item => item.signed)` while the stage test at `:85` is `manual.length > 0 && every(…)`. | With an empty manual list the button is live while the rail says incomplete — two contradictory statements about one fact on one screen. | Use the `allSigned` helper in both places.
7. MEDIUM | `SetupScreen.tsx:853-877` | `VideoMini` re-implements `VideoPanel` but drops fps, frame age, the latency-basis tooltip, the `feed.message` alert band and the `source === "SIMULATED"` cue; its `alt` is just `${callsign} feed` (`:863`). | The same physical feed presents far healthier in Stage 4 than in the mission wall — and Stage 4 is where hardware sign-off happens. | Delete `VideoMini`, render `VideoPanel` with a compact prop.
8. MEDIUM | `SetupScreen.tsx:220-234` | Stage buttons carry no `aria-current` or `aria-expanded`; completion is a `<Check>` glyph plus the word "Complete" (`:227-231`) — good — but the active stage is marked only by background and a 2 px left accent (`index.css:1014-1018`). | Add `aria-current="step"`.
9. LOW | `SetupScreen.tsx:1072` | `evidenceQuality(state)` computed twice in one render and shown only as a badge with the `MIXED (…)` string as its own label. | Logic is honest and correct; the issue is that the launch screen's most important provenance fact is a single small chip. | Promote to a `Notice` when not `LIVE`.
---
## 4. Mission command (`screens/MissionScreen.tsx`, `index.css:1214-1413,1834-1844`)
**Purpose.** The operational picture: fleet, kit ledger, delivery states, command lifecycle, map, video
wall, survivors, allocation, AI advisory, warnings, autonomy timeline. **Can:** select/expand a survivor,
expand the map and each video, query the local copilot. **Cannot:** command anything from the body — no
launch, hold, re-task or per-aircraft control exists here; the only commands in the whole station are
the top-bar abort and recall. A deliberate and defensible reduction of surface, but it means a screen
named "Mission command" issues no mission commands.
Design **8/10** — dense, restrained, and the rail work is genuinely good. Usability **6/10** — rail
segments are 2 px tall and their meaning is hover-only. Hierarchy **5/10** — the right rail puts a
zero-authority advisory panel above the safety panel, inverted for a stressed operator.
1. CRITICAL | `MissionScreen.tsx:471` (same pattern `ReplayScreen.tsx:412`) | Autonomy-timeline rows are `fmtClock` + a 7 px `timeline-dot` + `topic · summarised payload`, and `summarise()` (`:661-678`) returns detail/phase/state/reason but **never the severity word**. | Severity is carried by colour alone (`index.css:1786-1789`: ok/warn/danger/info), directly violating `DESIGN_SYSTEM.md:68-69` and the standing no-colour-only rule: an amber dot versus a red dot is the only difference between a warning and a critical interlock, and `--warn #e7b549` / `--danger #ef5f63` at 7 px are the two hues most commonly confused under stress and by colour-vision-deficient readers. | Prefix the severity word.
2. CRITICAL | `MissionScreen.tsx:325` and `:489` | `provenance={mission.mode === "REAL" ? "LIVE" : "SIMULATED"}`. | The chip derives from *mode*, not from telemetry: a REAL sortie whose aircraft are all `stale`, or whose `vehicle.data_quality` is `SIMULATED`, or which has received no heartbeat, still reads "LIVE TELEMETRY" in success green (`TacticalMap.tsx:437`). The codebase already knows better — `SetupScreen.tsx:50-56` deliberately refuses to call heartbeat-less REAL "LIVE" and reports `MIXED`. The aircraft cards honour per-vehicle `data_quality` (`:527-532`) while the map underneath them contradicts them. | Reuse `evidenceQuality()` and add a fourth state ("STALE — NOT FRESH TELEMETRY").
3. HIGH | `index.css:1839-1844` + `MissionScreen.tsx:603-612` | Phase-rail segments are `flex: 1; height: 2px; min-width: 4px`; in the 268 px left column (`index.css:1301`) minus 10 px panel padding and 8 px card padding (`:239`), 13 segments plus 12 × 2 px gaps give roughly **16 px × 2 px** each, and each segment's meaning ("break-beam confirmed" vs "no delivery evidence", `:609`) exists **only** as a `title` on that strip. | A 2 px target is not reliably hoverable, `title` is invisible on touch and to most keyboard users, and content inside `role="img"` (`:596`) is ignored by AT — so the single most important distinction in the delivery rail is the least reachable text on the screen. | 6-8 px segments plus a visible one-line release verdict.
4. HIGH | `MissionScreen.tsx:603-605` | Reached-but-unproven segments are `var(--accent)` `#f0a03c` and the no-evidence release segment is `var(--warn)` `#e7b549`. | Two adjacent yellow-oranges, one of them 2 px tall, must encode "this happened" versus "this is not proven" — the exact distinction the brief says must never be lost. The accompanying explanation at `:255-259` is honest but rendered `.micro`, 10 px, letter-spaced uppercase, in `var(--faint)` (`index.css:155-161`), about **2.8:1** dark / **2.4:1** light against `--surface-2`: the safety explanation is the least legible text on the screen. | Distinct shape for the unproven segment; caption in `.small`/`--muted`.
5. HIGH | `MissionScreen.tsx:413-443` vs `:445-461` | Right-rail order is Survivors, Allocation, **AI intelligence (explicitly "No command authority", `:417`)**, Warnings, Autonomy timeline; Warnings is `maxHeight: 190` inside a column that already scrolls (`index.css:1308-1316`). | A zero-authority advisory panel outranks the pad-interlock, separation, failsafe and link-lost panel, and the warnings panel needs a second scrollbar to read. Under load the operator reads the chat first. | Move Warnings directly under Survivors; copilot to the bottom or behind a tab.
6. MEDIUM | `MissionScreen.tsx:168` and `:121` | The "Recall active" badge and the critical recall warning are both gated on `mission.phase === "EXECUTING"`. | The engine sets COMPLETE/ABORTED (`mission_engine.py:678`) while aircraft may still be returning, so the indicator vanishes even though `App.tsx:78` still treats `mission.recall` as live — and the top bar keeps no recall indicator at all. | Drop the phase conjunct; render whenever `mission.recall` is truthy.
7. MEDIUM | `MissionScreen.tsx:116` vs `App.tsx:70` | `online = vehicles.filter(v => v.connected)` printed as "{online}/{vehicles.length} linked" (`:190`), while App computes the same label family as `connected && !stale` and prints "{online}/{vehicles.length} link" (`:212`). | Two counters with near-identical labels and different definitions about 90 px apart; the operator cannot tell which means "fresh". | One definition, one label, stale count in the caption.
8. MEDIUM | `MissionScreen.tsx:419-421,439` | The copilot answer area is a plain `div`, not a live region, with no "answering" state beyond the send-button glyph swapping to a text `◌` instead of the station's `Loader2`. | A slow answer appears unannounced and the placeholder prompt stays, so the operator cannot tell whether the displayed answer is stale. | `aria-live="polite"` plus an explicit "Composing answer…" state.
9. MEDIUM | `MissionScreen.tsx:343` | Panel title `Survivors · ${state.survivors.total}/10`. | Literal denominator while `max_survivors` sits in the same object (`types.ts:105`). | Use it.
10. MEDIUM | `index.css:1221-1230` | `.mission-header` is `display: flex` with **no** `flex-wrap`, holding identity, timer block, fleet block, spacer, mode badge, recording badge and a conditional Report button (`:163-209`), while the responsive rules only narrow the body columns (`index.css:2173-2178`). | Whether the header clips at 1280-1080 px is **not verifiable by static review**, but nothing in the CSS prevents it, and `DESIGN_SYSTEM.md:97-98` documents collapse at 1280 px where the code collapses at 1080 px. | Allow wrapping or move the recording badge into the video wall; correct the documented breakpoint.
---
## 5. Event log (`screens/LogsScreen.tsx`, `index.css:2077-2101,2143-2150`)
**Purpose.** Session evidence stream with filtering and export. **Can:** filter by severity, data
quality, source and free text; pick a row cap; export what is shown as NDJSON; refresh. **Cannot:**
retrieve events persisted before this station started, or see a raw payload.
Design **7/10** — competent table; the sticky 9 px uppercase `th` (`index.css:2082-2094`) is the
smallest heading text in the station. Usability **7/10** — filters labelled, empty state explains
itself. Hierarchy **7/10** — summary stats sit above the table where they belong.
1. HIGH | `LogsScreen.tsx:118-127` vs `:220` | `exportVisible` serialises **all** of `filtered` while the table renders `filtered.slice(0, limit)`. | The control is called "Export visible" and exports rows the operator is not looking at; an operator who sets the cap to "Last 100" and hands over the file believes it matches the screen. For an evidence artefact that mislabels the record. | Slice on `limit` too, or rename to "Export filtered".
2. MEDIUM | `LogsScreen.tsx:8` | `SEVERITIES` offers `debug`, but the backend emits only info, warning, error and critical (`events.py:142` default plus the 19 critical / 4 error / 27 warning sites). | A filter that can never match, in a station whose brief forbids controls that look functional; selecting it yields the empty state that then claims "Events are appended by the local backend", misleading the operator into waiting. | Derive the option list from data, as `sources` and `qualities` already are (`:84-91`).
3. MEDIUM | `LogsScreen.tsx:191-195` | Row caps include 800 and 2000, but the buffer holds at most 400 events (`GcsContext.tsx:116`). | Two of four options are indistinguishable and the "Events buffered" stat (`:132-135`) can never reach its advertised ceiling. | Offer achievable caps; state the buffer depth.
4. MEDIUM | `LogsScreen.tsx:62-77` | Nothing says the list is a **session buffer** rather than the durable log, while the panel is titled "Evidence stream" and boot promised "a complete local evidence trail" (`BootScreen.tsx:125`). | After a restart the operator may conclude earlier evidence is gone, or may cite the buffer as the record. | One line naming the buffer vs archive scope.
5. LOW | `LogsScreen.tsx:225` | `<Badge … className="sm">` — `.badge.sm` **does not exist** (`index.css:351-407` defines `.badge`, the tones and `.badge.pulse` only). | Silent no-op: severity chips render at default height in 11 px rows, inflating every row. | Add `.badge.sm` or use the inline `style` pattern the rest of the codebase uses.
6. LOW | `LogsScreen.tsx:208-217` | `<th>` elements carry no `scope="col"` and the table has no `<caption>`. | AT announces a grid of unlabelled cells.
---
## 6. Mission archive / replay (`screens/ReplayScreen.tsx`, `index.css:1972-2049`)
**Purpose.** Reconstruct a recorded sortie and rebuild its report. **Can:** pick a mission, scrub or
play at 1-120x, read analytics, altitude profile and event timeline, download or rebuild the PDF.
**Cannot:** change the live picture — and correctly, this screen offers no abort/recall in its own body.
Design **7/10** — the altitude sparkline and bar rows are clean. Usability **6/10** — the slider's value
is an epoch-millisecond number with no `aria-valuetext`. Hierarchy **6/10** — the "not live" signal is a
single 26 px chip on the map, and the "Evidence" panel that should carry it is third in the left rail.
1. HIGH | `ReplayScreen.tsx:39-75` | `emptyVehicle()` seeds every replayed aircraft with `connected: true`, `identity_valid: true`, `ekf_ok: true`, `gps_fix: 3`, `queue_depth: 0`, `kits_free: 0`, `stale: true`. | A recorded sortie has no link, no identity claim and no EKF state; asserting `connected: true` means the map never draws the offline marker (`TacticalMap.tsx:313`, `index.css:1543`) and the fleet looks uniformly healthy in an archive whose whole purpose is honest reconstruction. The `RECORDED` chip is the only thing between this and a false claim. | Seed `connected: false`; derive `identity_valid`/`ekf_ok` from the sample.
2. MEDIUM | `ReplayScreen.tsx:335-343` | The scrubber `input[type=range]` uses raw epoch-ms for `min`/`max`/`value` with `step={500}` and only `aria-label`. | AT reports a number such as 1.75e12; with no `aria-valuetext` the most-used control on the screen is unreadable non-visually. | Add `aria-valuetext={fmtClock(...)}` and a seconds-scaled step.
3. MEDIUM | `ReplayScreen.tsx:334` with `:123` | `fmtClock(new Date(playhead).toISOString())` when a record has no finite samples leaves `playhead` at 0, so the readout shows epoch wall time. | A fabricated-looking clock on a mission that never played. | Guard on `range.end > range.start`.
4. MEDIUM | `ReplayScreen.tsx:362` | `Survivors … {analytics.survivors_registered ?? 0}/10`; the analytics payload has no field for the configured cap. | Pure-assumption denominator. | Print the count alone or take the cap from the mission record.
5. LOW | `ReplayScreen.tsx:433` | Rebuild modal footer link is `href={missionId ? … : "#"}` with `download`. | Exactly the "looks functional, does nothing" pattern the brief forbids; latent, since `regenerate()` guards the null case. | Render a disabled button when there is no id.
6. LOW | `index.css:2011` | `.scrubber input[type="range"] { width: 100% }` with five siblings in one flex row and no `min-width: 0`. | Whether the track collapses at narrow widths is **not verifiable by static review**. | Add `flex: 1; min-width: 120px`.
---
## 7. Configuration (`screens/SettingsScreen.tsx`, `index.css:2051-2075`)
**Purpose.** Read-only view of the commissioned profile, interfaces and operating policy. **Can:**
refresh, and read. **Cannot:** edit anything — correctly stated on the screen (`:133`, `:342`).
Design **8/10** — the panel grid reads well and the policy prose is unusually candid. Usability **7/10**
— one Refresh control and no hidden actions, but the four `Promise.all` fetches (`:104-109`) fail
all-or-nothing. Hierarchy **7/10** — Operating policy first is the right call for a read-only screen.
1. HIGH | `SettingsScreen.tsx:259-266` | "Mission limits" prints configured values with no provenance and no "never physically tested" qualifier, though the required vocabulary includes `PENDING TEST` / `PHYSICALLY TESTED` and `qualityTone()` supports both (`format.ts:62-68`). | Grep across `desktop/src` finds those two strings **only inside `format.ts`** — nothing in the UI ever renders either. The station's central promise is that every value carries a provenance label, yet the screen where hardware assumptions are most likely to be read as fact is the one screen with none. | Add a `PENDING TEST` chip per unmeasured limit (descent clearance, release altitude, radio region).
2. MEDIUM | `SettingsScreen.tsx:203` | `Badge tone={live?.connected ? "ok" : "muted"}` printing "Offline". | The identical fact on the mission screen renders `danger` with the word "LOST" (`MissionScreen.tsx:522-523`). Same state, two severities, two words, one application. | One vocabulary for link loss; `danger` on both.
3. MEDIUM | `SettingsScreen.tsx:224` | `Commissioned` prints `Yes`/`No` in default ink with no tone, immediately above a warn Notice explaining that commissioning requires all three aircraft proven (`:227-231`). | "No" is the single most important fact on the screen for a REAL launch and is typographically indistinguishable from "Yes". | Tone the value or render a `Badge`.
4. MEDIUM | `SettingsScreen.tsx:100-119` | A single `Promise.all` over four endpoints; one failure sets `error` and leaves `config`, `info`, `devices`, `diagnostics` all null. | One message and eight empty panels: the pattern reads as "the station is broken", not "capture devices are unavailable". | Fetch independently with a per-panel error/empty state.
5. LOW | `SettingsScreen.tsx:85-89` | `checkTone()` returns `warn` for any status not exactly PASS or FAIL, including an absent string. | Conservative and honest, but an unknown looks identical to a genuine WARN. | Add a `muted` branch for empty/unknown.
---
## 8. App shell (`App.tsx`, `index.css:192-337`)
**Purpose.** Rail navigation, persistent top bar, emergency cluster, offline banner, confirm dialog,
toasts. **Can:** navigate all five screens, switch theme, read link/phase/mode, invoke abort or recall.
**Cannot:** do either of the last two before the station has a state object.
Design **8/10** — the icon rail with `aria-label` and `aria-current="page"` (`:164-165`) is the
best-a11y pattern in the app. Usability **6/10** — the disabled emergency buttons leave the tab order
and their reason is 10 px grey text. Hierarchy **7/10** — recall as a solid red gradient
(`index.css:2152-2159`) correctly reads as the strongest control in the bar.
1. HIGH | `App.tsx:222` and `:232` | `disabled={emergencyBlockReason !== null}` on ABORT and EMERGENCY RECALL, alongside `aria-disabled` (`:224`, `:234`). | Native `disabled` removes both from the tab order, so a keyboard or switch-access operator can neither reach them nor read the reason in `title`; the contradicting attributes also confuse AT. A safety control must stay focusable and announce why it is inert. | Drop `disabled`, keep `aria-disabled`, guard inside the handler, reason as visible text.
2. HIGH | `App.tsx:239-243` | The reason a safety button is unavailable is `<span className="micro" style={{ color: "var(--faint)", maxWidth: 150 }}>` printing literally "idle" or "link down", with the full explanation `title`-only. | Two problems: `.micro` + `--faint` computes to about **2.8:1** — effectively invisible; and when the block reason is `!connected` with nothing airborne (`:79-83`) the label says "idle", which is wrong — the service is unreachable. The operator's first question about a dead safety button gets a misleading one-word answer. | Render `emergencyBlockReason` verbatim at `--muted`/11 px minimum.
3. HIGH | `App.tsx:134-147` | `BootScreen` and the "Connecting to local ground service" screen return before the shell is built. | During those states **no abort and no recall affordance exists anywhere on screen**, and no statement of what to do instead — including the service-dies-mid-sortie case `:79-83` explicitly anticipates. The console shows a spinner with no safety path. | On the offline/loading path show a persistent band: onboard failsafes are the protection and this station is not the recall path.
4. MEDIUM | `App.tsx:204` vs `:218-238` | The informational mode badge uses `tone="danger"` for REAL and sits 8 px from the red abort/recall cluster (`index.css:321-325`, `gap: 8px`). | `DESIGN_SYSTEM.md:51` and `:67` reserve red for abort, recall and critical interlocks; an informational chip in the red channel, in the same 8 px rhythm as the destructive pair, dilutes the one colour that must mean "act". | `tone="accent"`, or give the cluster its own bordered group.
5. MEDIUM | `App.tsx:207` | The top-bar phase badge re-implements phase colour as a three-way ternary instead of calling `statusTone()`. | The engine's phase set is SETUP/PLANNING/INSPECTION/PREFLIGHT/ UPLOAD/READY/EXECUTING/COMPLETE/ABORTED (`mission_engine.py:83,146,216,232,265,343,370,678`), so `READY` renders `muted` in the top bar while `MissionScreen.tsx:167` renders the same `READY` through `statusTone` as `ok` — two adjacent badges stating one fact in two colours about 30 px apart. | Delete the ternary; call `statusTone(phase)`.
6. MEDIUM | `App.tsx:211` | The fleet link pip is `width: 5, height: 5` inline with no shape or word beyond "n/m link", relying on `.pip.ok/.warn/.danger` (`index.css:1295-1297`, verified present). | The recent fix works — the standalone `.pip` base has no background of its own, so the inline box is what makes it visible at all — but 5 px is colour-only fleet health at the top of every screen. | Raise to 8 px and add a word.
7. MEDIUM | `App.tsx:272-340` + `ui.tsx:128-154` | The abort/recall dialog has no focus trap and no focus restore, and its accessible name falls back to "Dialog" (§11 item 1). | `DESIGN_SYSTEM.md:131` claims "dialogs trap focus"; the code adds an `Escape` listener only, so Tab walks straight out of the confirmation dialog into the live top bar behind it — a real risk on the one dialog where the operator types a destructive token. | Implement a trap and restore focus to the trigger.
8. LOW | `App.tsx:142` | `<Loader2 className="spin" />` on the pre-state screen, but the only spin animation is scoped to `.btn .spin` (`index.css:491-493`). | That loader is a static plus-shaped glyph that looks like a broken control; `BootScreen.tsx:131` had to inline the keyframe to work around the same gap. | Add a global `.spin`.
9. LOW | `App.tsx:30-39` | No keyboard shortcuts exist anywhere (only `ui.tsx:133`, `MissionScreen.tsx:436`, `SetupScreen.tsx:598` handle keys). | Not a brief violation, but a single-operator stress console where navigation, abort and recall all require mouse or Tab traversal. | A documented key map with nothing destructive bound without the typed token.
---
## 9. TacticalMap (`components/TacticalMap.tsx`, `index.css:1415-1573`)
**Purpose.** Offline situational picture: AOI, lanes, geofences, pad, links, trails, vehicles,
survivors. **Can:** pan/zoom, fit to area, expand, click a survivor marker. **Cannot:** draw, measure or
select an aircraft — there is no vehicle click handler at all (`:308`, markers non-interactive).
Design **8/10** — restrained chips, procedural basemap, original glyph set, and the "not satellite
imagery" attribution (`:470-474`) is exemplary honesty. Usability **7/10** — the survivor marker is 16 px
carrying a 7 px label inside it. Hierarchy **8/10** — overlays sit at the corners and never cover the
centre.
1. HIGH | `TacticalMap.tsx:334-343` + `index.css:1558-1561` + legend `:464-468` | Markers draw four tones (delivered/unknown/unserved/assigned) plus a default accent for anything else, but the legend advertises only Search, Delivery, Delivered, Unknown, Assigned. | Two mismatches: the **red `unserved` marker has no legend entry**, so a survivor who has been failed for is a red disc with no key anywhere on screen; and the `Survivor` union includes `CONFIRMED` and `DUPLICATE` (`types.ts:87`), neither of which matches the ternary chain, so both render default accent — the same amber as the *delivery aircraft* swatch, making a CONFIRMED survivor read as an aircraft. | Add an Unserved row; map CONFIRMED to delivered and DUPLICATE to a distinct muted marker.
2. MEDIUM | `TacticalMap.tsx:306-317` | Marker markup is injected with `element.innerHTML` and updated via `querySelector`. | Safe today (only `vehicle.callsign` reaches `textContent`), but it places React-unmanaged DOM inside a MapLibre overlay whose lifecycle React does control (`:196-205`), and aircraft markers carry no `role` or `aria-label` — the entire air picture is invisible to AT with no text alternative anywhere near the map. | React-managed nodes plus an `aria-label` summary ("3 aircraft, 8 survivors") as the map's accessible content.
3. MEDIUM | `TacticalMap.tsx:350-351` | Survivor markers get `style.cursor = "pointer"` and a raw `addEventListener("click")`. | A 16 px click target not in the tab order with no keyboard equivalent; the same selection is already in the survivor list, so this is a less accessible duplicate of the real control. | Keep the list as the accessible path and mark the marker `aria-hidden`.
4. MEDIUM | `index.css:1415-1421` | `.map-panel { background: #071018 }` is a literal, as is the fallback layer at `TacticalMap.tsx:102`. | In the light theme the map letterbox stays near-black inside an otherwise white station, and `styleError` text at `:477` is `var(--muted)` (`#66788e` in light) on that black — a legitimate pairing by accident, not by design. | Tokenise the backdrop per theme.
5. LOW | `index.css:1562-1573` | `.pad-marker` is defined but never used — the pad is a GeoJSON polygon (`:161-173`). | Dead rule; more relevant, the pad has no on-map text label while survivors do, so "which circle is the pad" is answerable only from the amber dash pattern, which the legend does not list. | Delete or use the rule; add a PAD swatch.
---
## 10. VideoPanel (`components/VideoPanel.tsx`, `index.css:1575-1698`)
**Purpose.** One 5.8 GHz analog channel with its own health evidence. **Can:** see the frame, status,
fps, frame age, latency basis, expand. **Cannot:** record, mute or switch channel from the panel.
Design **9/10** — the `LATENCY_BASIS` table (`:16-22`) is the strongest honesty artefact in the
codebase. Usability **7/10** — five items in a `space-between` bar at 8.5 px. Hierarchy **8/10** —
watermark, then status, then numerics.
1. HIGH | `VideoPanel.tsx:44-45, 64, 72-76` + `:125-132` | `live` is derived from `feed.status`, and if the image request errors, `failed` latches and the panel swaps to `.no-feed` while still printing `humanStatus(status)` — which returns `"LIVE"` unchanged (`:131`). | The panel shows a black box captioned **LIVE**: a success word with no evidence behind it, which is the brief's forbidden dummy success state in REAL mode. The frame-request failure — the one fact this panel actually measured — is never displayed. | Add a `failed` branch rendering "NO FRAME RECEIVED" and drop the status word.
2. MEDIUM | `VideoPanel.tsx:96-106` + `index.css:1619-1633` | `.video-bottom` is a non-wrapping flex row of five items at `font-size: 8.5px`, the longest being the constant "NO AI OVERLAY · UNSYNCHRONISED", inside a three-up wall in the centre column (`index.css:1575-1580`, `1318-1323`). | Whether the row collides at 1280 px is **not verifiable by static review**, but the code makes the fixed string the longest of the five and gives the measured values (fps, age, latency) the least room — the reverse of what an operator needs. | Shorten the constant, move age/fps/latency to `.video-cues`, allow wrapping.
3. MEDIUM | `index.css:1658-1667` | `.no-feed { color: #53657c }` on `#04070b` computes to about **3.4:1** for 11 px text. | The dead-feed state — precisely when the reason must be read — is the lowest-contrast body text in the station. | Use `--ink-2`.
4. MEDIUM | `VideoPanel.tsx:50` | A 380 ms nonce re-requests `frame.jpg` per live panel, so three panels plus the `VideoMini` clones (`SetupScreen.tsx:857`, 480 ms) generate continuous image fetches on the same loopback port that serves state polling and WebSocket frames. | Whether this starves state updates under load is **not verifiable by static review**, but the design competes with the safety path for one port. | Prefer `multipart/x-mixed-replace` or `fetch` + `createImageBitmap`.
5. LOW | `VideoPanel.tsx:107-120` | Alert band and cue row appear only for frozen/degraded/UNMAPPED, never for `feed.recording === false` in REAL mode; "0/3 recording" surfaces once in the mission header (`MissionScreen.tsx:202-204`) as a `muted` badge. | An evidence-recording failure is quiet. | Add a per-panel "NOT RECORDING" cue — recording *is* the evidence trail.
---
## 11. Shared UI primitives (`components/ui.tsx`, `lib/format.ts`, `index.css` tokens)
Design **8/10**. Usability **6/10** — the primitives are the source of most station-wide contrast and
focus problems. Hierarchy **7/10** — `Badge`/`Panel`/`Meter` are well-scoped; `Modal` under-delivers on
its documented contract.
1. CRITICAL | `ui.tsx:140` | `aria-label={typeof title === "string" ? title : "Dialog"}`. | `App.tsx:274-279` passes a JSX element (ShieldAlert + text) as the abort/recall title, so the dialog's accessible name is the literal string **"Dialog"** and the `<h2>` at `:143` is never linked via `aria-labelledby`. `ReplayScreen.tsx:433` and `SetupScreen.tsx:377` pass strings and name correctly, so the bug bites only on the safety path: the station's most dangerous confirmation surface is announced to a screen reader as an unnamed modal. | Render the title into an `id`, use `aria-labelledby`, drop the type branch.
2. HIGH | `ui.tsx:128-135` | The only keyboard behaviour is a global `window` `Escape` listener. | No focus trap, no initial focus management, no restoration, no `inert`/`aria-hidden` background — contradicting `DESIGN_SYSTEM.md:131`. Also unguarded: every mounted `Modal` registers on `window`, so `MissionScreen.tsx:481` and `:495` close on the same keypress while sitting behind the abort dialog. | Trap focus; scope the listener to the topmost dialog.
3. HIGH | `index.css:116-119` | `button:disabled { opacity: 0.45 }` applied station-wide. | On `.btn.emergency` (`index.css:2152-2159`) that is a solid red panel reduced to 45 % over a dark surface, so the two most important controls in the station are the **dimmest** objects in the top bar whenever they are inert. `DESIGN_SYSTEM.md:124` promises unsafe buttons are "disabled with a visible reason, not hidden" — the reason is present but visually hidden instead. | Border/label treatment; keep the destructive hue.
4. HIGH | `ui.tsx:139` | `onMouseDown` on the backdrop closes the dialog. | On the abort dialog this silently discards a typed "ABORT" and the reason string (`App.tsx:99-101` resets on reopen). A stray press on the dim area during a stress event loses the operator's confirmation work. | No backdrop dismissal for destructive dialogs.
5. HIGH | type scale: `index.css:155-161, 604-610, 1049-1051, 1529-1535, 1554-1556, 1630, 1651, 2001, 2074-2075, 2084-2088` with inline overrides at `MissionScreen.tsx:242` (8 px), `:287`, `:368`, `:522`, `:528` (7.5 px), `:454-455`, `:508` and `ReplayScreen.tsx:271` (7.5 px) | `DESIGN_SYSTEM.md:79` sanctions 8-10 px for micro labels and the implementation goes below it: badges carrying LINK, STALE, LOST, DELIVERED, UNKNOWN and the command-lifecycle label render at **7.5 px, uppercase, weight 700, letter-spaced** — under any defensible threshold for a single-operator safety console read at arm's length in sunlight. That the token is 7.5 px is verifiable from source; whether a specific badge is legible is **not verifiable by static review**. | Floor the badge scale at 9.5 px; stop letter-spacing uppercase below 10 px.
6. HIGH | contrast from `index.css` literals | `--faint #4d5f78` (`:35`) on `--surface-2 #0d1624` (`:26`) is about **2.8:1**; light `--faint #92a1b3` (`:62`) on `--surface-2 #f1f5f8` (`:53`) is about **2.4:1**. `--faint` carries the phase-rail explanation (`MissionScreen.tsx:255`), the lifecycle explanation (`:273`), the allocation tie-break rule (`:408`), the LLM-authority statement (`:442`), `MiniStat` captions (`:508`), `stage-status` (`index.css:1050`), survivor coordinates (`:376`) and the disabled-safety-button reason (`App.tsx:240`). Rendered ratios depend on the `--panel` alpha cascade and are **not verifiable by static review**, but every plausible pairing of these literals fails AA for text. | Reserve `--faint` for hairlines and non-informational chrome; promote the above to `--muted` (about 5.2:1 dark).
7. MEDIUM | `format.ts:44-55` | `statusTone()` is one function over three disjoint vocabularies: survivor/assignment statuses, mission phases (none of SETUP…UPLOAD is listed, so they fall through to `muted`) and delivery-task states. | That merge is the direct cause of the READY conflict in §8 item 5, and an unrecognised phase silently reads `muted` rather than visibly unmapped. | Split `phaseTone`/`taskTone`/`survivorTone` with a distinct UNMAPPED treatment.
8. MEDIUM | `index.css:1839-1844` with `:31` | Rail segments at `height: 2px` and the not-yet-reached state as `var(--line-strong)` = `rgba(148,177,211,.26)`. | The untraversed part of a delivery rail is near-invisible, so "how far along is this kit" is answerable only from the filled portion, and `--line-strong` is the same value used for hairline borders — the rail reads as a separator, not a gauge. | 6-8 px height and a distinct unfilled token.
9. MEDIUM | no `@media (prefers-reduced-motion)` rule exists anywhere in `index.css` (grep: zero matches) while the app ships a continuous radar sweep (`:835-847`), infinite pulsing dots (`:405-413`), an animated scanline overlay (`:1597-1603`) and `toast-in`/`modal-in` keyframes. | `DESIGN_SYSTEM.md:120-122` documents motion as a design feature but there is no reduced-motion escape hatch — an accessibility gap and a real cost on a low-power field laptop. | Wrap infinite animations in `@media (prefers-reduced-motion: no-preference)`.
10. MEDIUM | `index.css:179-189` | `.sr-only` is defined and **never used** in any `.tsx`; grep across `desktop/src` finds exactly two `aria-live` sites, both `App.tsx:342,344`. | The one utility class built for accessibility is unused while the map, the phase rails, the video wall and the mission timer — the four things an operator watches — have no text alternative or live region at all. | Use it for map and rail summaries; set `aria-live="off"` where 1.4 s churn would spam AT.
11. LOW | `ui.tsx:73-97` | `Meter` renders a value bar with no `role="progressbar"`, `aria-valuenow` or min/max. | Battery state is announced as nothing; the paired numeric text (`MissionScreen.tsx:537`) is the only accessible form, so the primitive is a decoration. | Add the semantics or `aria-hidden` it.
12. LOW | `ui.tsx:113-154` | `Modal` sets no `aria-describedby`, and no primitive in `ui.tsx` accepts one. | The abort dialog's four-sentence consequence text (`App.tsx:296-305`) is read only if the user happens to navigate into it. | Link body text with `aria-describedby`.
---
## 12. Cross-cutting human-factors findings
**Colour-only signalling.** Two genuine violations survive the recent fixes: the autonomy-timeline
severity dot at `MissionScreen.tsx:471` and `ReplayScreen.tsx:412` (7 px, colour, no word), and the map
legend that omits `unserved` and collapses `CONFIRMED`/`DUPLICATE` into the aircraft amber
(`TacticalMap.tsx:334-343,464-468`). Elsewhere the rule is honestly kept — every badge pairs a hue with a
word, and `qualityTone`/`txTone` were deliberately built so an ACK never renders green
(`format.ts:62-101`). The residual risk is the adjacent-hue pair `--accent` vs `--warn` on a 2 px rail
(§4 item 4).
**Contrast and font sizes read from `index.css`.** Body 14 px; `.micro` 10 px / weight 700 / 0.12em /
uppercase (`:155-161`); `.stat .label` 9.5 px (`:604-610`); `.badge` 10.5 px with inline overrides down to
7.5 px; `.video-bottom` 8.5 px; `.video-cues` 8 px; `.map-marker .tag` 8 px; `.marker-survivor` label 7 px;
`.log-table th` 9 px; `.mission-item .dt` 9 px. Literal-token ratios: `--faint`/`--surface-2` about 2.8:1
dark and 2.4:1 light; `.splash-check .state` about 4.1:1; `.no-feed` about 3.4:1; light-theme
`.notice.warn` about 4.3:1. All are **computed from literals** and the rendered pairs are **not verifiable
by static review**. The systemic pattern is specific: the station's most important *explanatory* prose is
routed through its *weakest* type treatment.
**Status and legend consistency.** Four separate sources of truth for one fact: the top-bar ternary
(`App.tsx:207`), `statusTone` (`format.ts:44-55`), local `safetyTone` (`SetupScreen.tsx:440-449`) and
`checkTone` (`SettingsScreen.tsx:85-89`). Consequences: READY is green on one screen and grey 30 px away;
link loss is "LOST"/danger on the mission screen but "Offline"/muted on Configuration (`:203`); warn is
`TriangleAlert` on boot, `ShieldAlert` in setup, badge-text-only in settings. The map legend is the only
place a colour key is documented, and it is incomplete.
**Mission-screen information density.** `index.css:1301` gives a 268 px left column holding four stacked
panels (Fleet, Kit ledger, Delivery state, Command lifecycle), two of which contain internally-scrolled
lists of 6 and 14 rows at 7.5-10 px (`MissionScreen.tsx:237-251`, `:280-307`); the right column is 330 px
holding five panels including an LLM chat (`:413-443`) with a `maxHeight: 190` scroll area below it
(`:445-461`). Internally-scrolled areas inside an already-scrolling column (`index.css:1314`) produce
nested scrollbars, and the newest-100 transaction truncation notice (`:309-314`) is the fourth disclaimer
in that column. The body is at or past the density one operator can sustain under time pressure; the
advisory panel is the clearest candidate to leave the default view.
**Keyboard reachability.** No shortcuts exist (§8 item 9). Positive: rail buttons are 44 px with
`aria-label` and `aria-current` (`App.tsx:160-169`, `index.css:228-239`); `:focus-visible` gives a 2 px
accent ring station-wide (`index.css:121-124`); the drop-zone is tabbable; `.input` has a 36 px min-height
(`index.css:669-693`). Negative: disabled safety buttons leave the tab order entirely (`App.tsx:222,232`);
map markers are not focusable (`TacticalMap.tsx:350-351`); no skip link exists from rail to body; and
whether the 28 px `.btn.sm` (`index.css:415-493`) clears the documented 24 px minimum after the cascade is
**not verifiable by static review**.
**Modal focus and ARIA.** Covered in §11 items 1-4: unnamed safety dialog, no trap, no restore, backdrop
dismissal of a destructive confirmation, and `autoFocus` used as the entire initial-focus strategy
(`App.tsx:334`, `SetupScreen.tsx:395`) — which works only because `Modal` returns `null` when closed so
each open remounts. No `aria-describedby` pattern exists anywhere, so no control in the station has
linked help text.
**Error and empty states.** Genuinely strong: the offline banner states that last state is "for reference
only" and that commands cannot be transmitted (`App.tsx:248-261`), and survivor, allocation, delivery,
transaction, timeline, log, archive and analytics each have written-out empty states naming the reason
rather than "no data" (`MissionScreen.tsx:235, 278, 354, 399, 466`; `LogsScreen.tsx:201-205`;
`ReplayScreen.tsx:260, 397, 407`). Two weak spots: the Settings all-or-nothing `Promise.all` (§7 item 4)
and the video panel's latched `failed` state, which converts an error into a success word (§10 item 1).
**Refresh latency — and there is no 700 ms poll.** Source values: `GcsContext.tsx:87` polls `/api/state`
every **1400 ms**; `:117-118` debounces a WebSocket-triggered refresh to **350 ms** after any `event`
frame and pushes `snapshot` frames immediately; `:124-127` retries a dropped socket after **2500 ms**;
`:116` caps the UI buffer at 400 events (`slice(-399)`) against a backend deque of 500 (`events.py`
`_recent`); `SetupScreen.tsx:105` re-reads diagnostics every **6000 ms**; `BootScreen.tsx:85` retries every
**4000 ms**; `MissionScreen.tsx:73` ticks the clock every **1000 ms**; `VideoPanel.tsx:50` re-requests
frames every **380 ms** and `SetupScreen.tsx:857` every **480 ms**; toasts dismiss at 6200 ms
(`GcsContext.tsx:60-64`). The only `700` in the frontend is the map `fitBounds` animation
(`TacticalMap.tsx:240`). Human-factors consequence: a separation, failsafe or link-loss event is normally
visible within 350 ms via the event path, but once the socket has dropped the same fact arrives on the
1400 ms poll, so the worst case is roughly 3.9 s of silent staleness before the offline banner appears —
on a console whose `airborne` test (`App.tsx:77`) also feeds the emergency-enable decision. At 8 m/s that
is about 31 m of unviewed travel. The mitigation already exists in code (per-aircraft heartbeat age,
`MissionScreen.tsx:552-565`) and should be promoted to the top bar.
**Safety-action reachability.** Verified against source: ABORT and EMERGENCY RECALL are rendered once, at
`App.tsx:218-244`, inside `.topbar`, which is mounted for all five screens (`:263-269`) — so they are
physically present on Setup, Mission, Replay, Logs and Settings and the brief's requirement is met at the
shell level. `emergencyLive` (`:78`) correctly widens beyond `EXECUTING` to any armed or >1.5 m AGL
aircraft or any active recall. Three caveats recorded above: absent entirely on the boot and pre-state
paths (§8 item 3), non-focusable when inert (item 1), and carrying a misleading one-word reason (item 2).
**Placeholder, dummy-state and originality audit.** No dummy success state was found in REAL mode beyond
the video `LIVE`-over-black case (§10 item 1). The `href="#"` (§6 item 5) is latent, and the two genuinely
inert controls are the `debug` severity option and the 800/2000 row caps (§5 items 2-3). The interface
stays a control station, not a chatbot: exactly one free-text input exists and it is explicitly labelled
"Read-only / No command authority / LLM never reaches MAVLink or the release path"
(`MissionScreen.tsx:416-417, 442`); its placement cost is the layout issue in §4 item 5, not a framing
problem. On originality, nothing proprietary is reproduced — the rail/top-bar/panel composition, `Logo`
and `AircraftGlyph` (`ui.tsx:173-217`), the radar splash (`index.css:804-833`) and the procedural-basemap
attribution are original; `DESIGN_SYSTEM.md:4-6` cites DJI FlightHub, Apple and Tesla as inspirations
only, and no reviewed source replicates a screen, mark or asset from those products.
---
## 13. Prioritised UI fix list
| # | Sev | Owning file | Change |
|---|---|---|---|
| 1 | CRITICAL | `desktop/src/components/ui.tsx` | Name the dialog from a rendered `id` via `aria-labelledby`; the abort/recall dialog must not announce as "Dialog" (§11.1). |
| 2 | CRITICAL | `desktop/src/screens/MissionScreen.tsx` | Derive map `provenance` from `data_quality`/`stale`/`connected` (reuse `SetupScreen.evidenceQuality`), not from `mission.mode`; add a STALE chip (§4.2). |
| 3 | CRITICAL | `desktop/src/screens/SetupScreen.tsx` | Replace the hard-coded Stage-2 profile numbers and the Stage-3 prose constants with config/plan values plus provenance chips (§3.1, §3.2). |
| 4 | CRITICAL | `desktop/src/screens/MissionScreen.tsx` | Put the severity **word** in autonomy-timeline rows; no 7 px dot carrying warning-vs-critical (§4.1, and the same row in ReplayScreen). |
| 5 | HIGH | `desktop/src/index.css` | Floor the badge/micro scale (no 7.5-8 px uppercase status text), re-tone informational `--faint` uses, add `prefers-reduced-motion`, add a global `.spin`, tokenise `.splash` and `.map-panel` (§11.5, §11.6, §11.9, §8.8, §9.4, §2.1). |
| 6 | HIGH | `desktop/src/components/ui.tsx` | Focus trap, focus restore, topmost-only Escape, no backdrop dismissal on destructive dialogs (§11.2, §11.4, §11.12). |
| 7 | HIGH | `desktop/src/App.tsx` | Replace native `disabled` with `aria-disabled` plus in-handler guard so ABORT/RECALL stay focusable and self-explain (§8.1, §11.3). |
| 8 | HIGH | `desktop/src/App.tsx` | Render `emergencyBlockReason` as visible `--muted` text instead of a 10 px `--faint` "idle"/"link down" (§8.2). |
| 9 | HIGH | `desktop/src/App.tsx` | Add a fallback-instruction band on the boot and pre-state paths: no recall path exists here, onboard failsafes are the protection (§8.3). |
| 10 | HIGH | `desktop/src/components/VideoPanel.tsx` | On frame-request error render "NO FRAME RECEIVED" and suppress the status word — never `LIVE` over a black panel (§10.1). |
| 11 | HIGH | `desktop/src/screens/MissionScreen.tsx` + `index.css` | Phase-rail to 6-8 px segments, distinct unfilled token, break-beam verdict as a visible caption not a `title` (§4.3, §4.4, §11.8). |
| 12 | HIGH | `desktop/src/screens/SetupScreen.tsx` | Drop `role="button"` from the drop-zone and name it, add Space; `id`/`htmlFor` on START; `aria-current="step"` on stages (§3.4, §3.5, §3.8). |
| 13 | HIGH | `desktop/src/screens/SetupScreen.tsx` | Take the fleet, video and survivor denominators from `state.video.total` / `max_survivors` / `vehicles.length` (§3.3). |
| 14 | HIGH | `desktop/src/screens/LogsScreen.tsx` | Make "Export visible" match what is visible (§5.1). |
| 15 | MEDIUM | `desktop/src/screens/MissionScreen.tsx` | Reorder the right rail: Warnings above the AI advisory (§4.5). |
| 16 | MEDIUM | `desktop/src/components/TacticalMap.tsx` | Complete the legend (Unserved, Pad) and give CONFIRMED/DUPLICATE their own marker tones (§9.1). |
| 17 | MEDIUM | `desktop/src/App.tsx` | Call `statusTone(phase)` in the top bar, share one `online` definition with MissionScreen, use `accent` for the mode chip (§8.4, §8.5, §4.7). |
| 18 | MEDIUM | `desktop/src/screens/SettingsScreen.tsx` | Per-endpoint fetches with per-panel errors; tone `Commissioned`; match the LOST vocabulary; add `PENDING TEST` chips (§7.1-§7.4). |
| 19 | MEDIUM | `desktop/src/screens/LogsScreen.tsx` | Derive severity options and row caps from real data; state the session-buffer scope (§5.2-§5.4). |
| 20 | MEDIUM | `desktop/src/screens/ReplayScreen.tsx` | Stop seeding `connected`/`identity_valid`/`ekf_ok`/`gps_fix` as healthy; `aria-valuetext` on the scrubber; guard the epoch-zero clock (§6.1-§6.4). |
| 21 | MEDIUM | `desktop/src/screens/BootScreen.tsx` | `aria-live` on the check list, `.state.fail`, wrapped detail text, degraded-warn acknowledgement at the gate (§2.2-§2.5). |
| 22 | MEDIUM | `desktop/src/screens/MissionScreen.tsx` | `aria-live` plus an explicit composing state for the copilot answer (§4.8). |
| 23 | LOW | `desktop/src/lib/format.ts` | Split `statusTone` into phase/task/survivor tones with an explicit unmapped branch (§11.7). |
| 24 | LOW | `desktop/src/screens/SetupScreen.tsx` | Delete `VideoMini` and reuse `VideoPanel` (§3.7). |
| 25 | LOW | `desktop/src/screens/LogsScreen.tsx` | Add `.badge.sm` or drop the dead class; add `th scope` and a caption (§5.5, §5.6). |
| 26 | LOW | `desktop/src/screens/MissionScreen.tsx` | `Survivors · total/max_survivors`; drop the phase conjunct on the recall badge (§4.6, §4.9). |
| 27 | LOW | `desktop/src/screens/ReplayScreen.tsx` | Replace `href="#"` with a disabled button (§6.5). |
---
## 14. Verified correct — do not re-review
Checked against source; all of these hold, so a later pass need not redo them.
1. **`DELIVERY_SPINE` matches the backend exactly.** All 13 rail entries (`MissionScreen.tsx:36-43`) appear in `delivery.py:17-21` in the same order with `HOLD_CLEARANCE` correctly **before** `TRANSIT`; `UNKNOWN` co-slotted onto `CONFIRMED` (`:42`) is right per `ALLOWED` (`delivery.py:36-38` — both are release outcomes and both advance to `CLIMB`); `ABORTED` is handled off-spine (`:582-603`) rather than invented as a step; `RELEASE_ELIGIBLE = {"HOVER_VERIFY"}` is respected.
2. **Command lifecycle is honestly modelled.** `txTone` (`format.ts:77-84`) gives ACKNOWLEDGED → `warn`, never `ok`; `txMeaning` (`:90-101`) states plainly that an ACK is not proof of execution; `TxRail` (`MissionScreen.tsx:623-640`) leaves the third segment open until VERIFIED. Every state and outcome matches `telemetry.py:41-47, 203-206, 561, 573, 618, 634-666, 686-700, 715-730`, and `TERMINAL_TX_STATES` agrees with the panel's unverified count (`:96-99`).
3. **The `pip` fix is real and load-bearing.** `.pip.ok/.warn/.danger` (`index.css:1295-1297`) exist and are the only colour source for the standalone top-bar pip (`App.tsx:211`), which has no `.fleet-dot` ancestor. `statusTone` now covers CONFIRMED and the delivery/video states (`format.ts:44-55`).
4. **Boot warn glyph is fixed.** `TriangleAlert` for `warn` (`BootScreen.tsx:135`) plus `.splash-check .state.warn` (`index.css:939`) are both present, and the comment at `:133-134` explains why a coloured tick would have been wrong.
5. **Time cap and mission-screen denominators come from state.** `MissionScreen.tsx:114` reads `mission.time_cap_min ?? 30` and names the clock the cap applies to (`:176`); `:190` uses `state.video.total`, `state.video.live_count` and `vehicles.length` (`types.ts:291-292`). Scope caveat: SetupScreen was not included in that change (§3.3).
6. **The map `provenance` chip exists with correct wording.** `TacticalMap.tsx:433-443` renders the three distinct strings required and ReplayScreen passes `"RECORDED"` (`:322`). Only the MissionScreen derivation is wrong (§4.2).
7. **Emergency enablement is genuinely defensive.** `App.tsx:71-83` keeps abort/recall live on `armed`, on `agl_m > 1.5`, or on any active recall, independent of mission phase — the right call given `mission_engine.py:678` ends the phase before the aircraft are necessarily down.
8. **Video latency honesty.** `VideoPanel.tsx:16-22` refuses to print an unmeasured link delay and explains each basis code; `:59-60` renders `LAT —` rather than a plausible number; `:98` states "NO AI OVERLAY · UNSYNCHRONISED" as a standing fact; `ageLabel` (`:24-32`) distinguishes "no frame yet" from unmeasured age.
9. **Copilot containment is stated consistently** in three places: `MissionScreen.tsx:416-417`, `:442`, and `SettingsScreen.tsx:166-169`.
10. **Simulated data is never presented as real** in the video wall, aircraft cards, log table or launch record: watermark keyed to `mode === "SIMULATION"` (`index.css:1685-1698`, `VideoPanel.tsx:95`, `SetupScreen.tsx:874`), per-vehicle `data_quality` badge (`MissionScreen.tsx:527-532`), `qualityTone` column (`LogsScreen.tsx:230-231`), and the `MIXED (…)` aggregate at `SetupScreen.tsx:50-56` whose comment explicitly rejects calling an un-heartbeat-ed REAL session "LIVE".
11. **Startup diagnostic ids match the backend.** `BootScreen.tsx:53-66` maps `backend/database/storage/map/safety/video_service`; `system.py` emits exactly those plus `capture/radio/serial`, and only PASS/WARN/FAIL, all handled (`:49-60`). Unrecognised ids degrade to "Not reported by this backend build" (`:65`), never to a tick.
12. **No fake progress and no fake readiness.** `ProgressRing` (`ui.tsx:219-251`) is driven by measured elapsed against the enforced cap and `Meter` by `battery_pct`; the Setup Stage-0 safety card shows `UNKNOWN` until the engine answers, with `SetupScreen.tsx:433-438` explaining exactly why a fixed green "ARMED" would have been a false claim (`DESIGN_SYSTEM.md:126`).
13. **The offline banner is correctly worded** (`App.tsx:248-261`): names what is stale, states that commands cannot be transmitted, and offers a retry rather than pretending to reconnect.
14. **Log filters are honestly scoped** — "filters apply to the buffer" (`LogsScreen.tsx:196-198`) and `:71-73` explains why raw telemetry frames are excluded instead of hiding the exclusion; sources and qualities are derived from the data (`:84-91`).
15. **Rail navigation a11y is correct**: real buttons with `aria-label`, `aria-current="page"` and a 44 px target (`App.tsx:160-169`, `index.css:228-239`), plus a labelled landmark at `:153`.
16. **Both typed gates require a token, not a click** (`App.tsx:116`, `SetupScreen.tsx:383`), their labels are correctly associated (`App.tsx:314`, `:325`), and the abort dialog states truthfully that recall does not disable onboard failsafes and that a sent command is not proof of an aircraft response (`App.tsx:296-312`) — the single best sentence in the UI.
