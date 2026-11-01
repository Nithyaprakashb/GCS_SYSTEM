import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  CircleDot,
  ClipboardList,
  Clock3,
  Crosshair,
  MapPin,
  Package,
  PlaneTakeoff,
  Radio,
  Send,
  Sparkles,
} from "lucide-react";
import clsx from "clsx";
import { api } from "../lib/api";
import { batteryTone, evidenceQuality, fmtClock, fmtDistance, fmtTime, humanise, qualityTone, statusTone, txMeaning, txTone } from "../lib/format";
import type { Assignment, CommandTransaction, DeliveryTask, Survivor, Vehicle } from "../lib/types";
import { AircraftGlyph, Badge, Empty, KV, Meter, Modal, Panel, ProgressRing } from "../components/ui";
import { TacticalMap } from "../components/TacticalMap";
import { VideoPanel } from "../components/VideoPanel";
import { useGcs } from "../state/GcsContext";

/**
 * The delivery state machine, in the order the backend actually walks it
 * (STATES/ALLOWED in backend/rescueswarm/delivery.py). Kept here as an explicit spine
 * rather than a slice: the previous list dropped ACK_PENDING, CONFIRMED, UNKNOWN and
 * ABORTED and had HOLD_CLEARANCE after TRANSIT, so a confirmed delivery rendered as an
 * empty rail and an out-of-order one rendered as further along than it was.
 *
 * CONFIRMED and UNKNOWN occupy the same slot — they are the two possible outcomes of the
 * break-beam check, not sequential steps. ABORTED is off-spine and handled separately.
 */
const DELIVERY_SPINE = [
  "QUEUED", "LAUNCHING", "HOLD_CLEARANCE", "TRANSIT", "DESCENT", "HOVER_VERIFY",
  "ACK_PENDING", "RELEASE_SENT", "CONFIRMED", "CLIMB", "RETURNING", "LANDING", "STANDBY",
];
const SPINE_SLOT: Record<string, number> = {
  ...Object.fromEntries(DELIVERY_SPINE.map((state, i) => [state, i])),
  UNKNOWN: DELIVERY_SPINE.indexOf("CONFIRMED"),
};

/**
 * The three links of the command lifecycle that the rail shows. `verified` is drawn as its
 * own segment precisely so that an accepted-but-unconfirmed command reads as an unfinished
 * rail rather than as a closed one.
 */
const TX_STEPS = ["sent", "ack", "verified"] as const;

function txStage(state: string): { stage: number; outcome: "open" | "ok" | "warn" | "fail" } {
  const s = state.toUpperCase();
  if (s === "VERIFIED" || s === "COMPLETED") return { stage: 3, outcome: "ok" };
  if (s === "ACKNOWLEDGED") return { stage: 2, outcome: "open" };
  if (s === "ACK_UNVERIFIED") return { stage: 2, outcome: "warn" };
  if (["REJECTED", "TIMEOUT", "FAILED"].includes(s)) return { stage: 1, outcome: "fail" };
  if (s === "CREATED") return { stage: 0, outcome: "open" };
  return { stage: 1, outcome: "open" };
}

export function MissionScreen({ onOpenReplay }: { onOpenReplay: (missionId: string) => void }) {
  const { state } = useGcs();
  const [now, setNow] = useState(Date.now());
  const [selected, setSelected] = useState<Survivor | null>(null);
  const [mapOpen, setMapOpen] = useState(false);
  const [videoOpen, setVideoOpen] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const mission = state?.mission;
  const vehicles = useMemo(() => {
    if (!state) return [] as Vehicle[];
    const list = Array.isArray(state.fleet.vehicles) ? state.fleet.vehicles : Object.values(state.fleet.vehicles);
    return [...list].sort((a, b) => (a.role === "SEARCH" ? -1 : b.role === "SEARCH" ? 1 : a.callsign.localeCompare(b.callsign)));
  }, [state]);
  const survivors = state?.survivors.survivors ?? [];
  const tasks = useMemo(() => {
    if (!state) return [] as DeliveryTask[];
    return Array.isArray(state.deliveries.tasks) ? state.deliveries.tasks : Object.values(state.deliveries.tasks);
  }, [state]);
  const assignments = state?.allocations.assignments ?? [];
  // Command lifecycle, surfaced verbatim from the telemetry registry. The task badges above
  // describe what the mission engine decided; these records describe what the aircraft
  // actually answered, and the two must never be collapsed into one green tick.
  const transactions = useMemo(() => {
    if (!state) return [] as CommandTransaction[];
    return Array.isArray(state.fleet.transactions) ? state.fleet.transactions : [];
  }, [state]);
  const unverifiedTx = useMemo(
    () => transactions.filter((tx) => !["VERIFIED", "COMPLETED", "REJECTED", "TIMEOUT", "FAILED"].includes(tx.state.toUpperCase())),
    [transactions],
  );

  if (!state || !mission) {
    return <div className="empty" style={{ height: "100%" }}>Waiting for mission state…</div>;
  }

  const start = mission.started_at ? new Date(mission.started_at).getTime() : null;
  const end = mission.ended_at ? new Date(mission.ended_at).getTime() : now;
  const wallElapsed = start ? Math.max(0, (end - start) / 1000) : mission.elapsed_s;
  // The 30-minute cap is enforced against simulated flight time, so the
  // operator-facing timer must show the same clock rather than wall time.
  const simElapsed = mission.simulation && mission.simulation.sim_time_s > 0 ? mission.simulation.sim_time_s : null;
  const elapsed = simElapsed ?? wallElapsed;
  // Read the cap the engine enforces instead of printing a constant: a profile with a
  // different time_cap_min used to show a countdown to a limit that was never in force.
  const timeCap = (mission.time_cap_min ?? 30) * 60;
  const timerTone = elapsed > timeCap * 0.9 ? "danger" : elapsed > timeCap * 0.75 ? "warn" : "";
  const online = vehicles.filter((vehicle) => vehicle.connected).length;
  const activeTask = (callsign: string) => tasks.find((task) => task.callsign === callsign && !["STANDBY", "CONFIRMED", "UNKNOWN", "ABORTED"].includes(task.state));

  const warnings = useMemo(() => {
    const items: { title: string; detail: string; severity: "warn" | "critical" | "info" }[] = [];
    if (mission.recall && mission.phase === "EXECUTING") items.push({ title: "Emergency recall active", detail: "All aircraft returning to the launch pad.", severity: "critical" });
    if (state.safety.pad.violation) items.push({ title: "Pad interlock", detail: "More than one armed aircraft is on the pad.", severity: "critical" });
    for (const sep of state.safety.separations ?? []) {
      items.push({
        title: `Separation ${sep.pair.replace("-", " / ")}`,
        detail: `${fmtDistance(sep.horizontal_m)} horizontal${sep.vertical_m !== null ? ` · ${sep.vertical_m.toFixed(1)} m vertical` : ""} — below the 8 m proximity threshold.`,
        severity: "warn",
      });
    }
    for (const [callsign, boundary] of Object.entries(state.safety.boundaries ?? {})) {
      if (boundary.available && boundary.warning) items.push({ title: `${callsign} near boundary`, detail: `${fmtDistance(boundary.distance_m ?? boundary.nearest_m ?? 0)} from the inclusion fence.`, severity: "warn" });
    }
    for (const vehicle of vehicles) {
      if (vehicle.failsafe) items.push({ title: `${vehicle.callsign} failsafe`, detail: vehicle.failsafe, severity: "critical" });
      if (!vehicle.connected) items.push({ title: `${vehicle.callsign} link lost`, detail: "No fresh heartbeat. Last known state remains displayed, never assumed safe.", severity: "critical" });
      else if (vehicle.stale) items.push({ title: `${vehicle.callsign} telemetry stale`, detail: "Aged beyond the freshness threshold.", severity: "warn" });
      if (vehicle.battery_pct !== null && vehicle.battery_pct < 25) items.push({ title: `${vehicle.callsign} battery ${vehicle.battery_pct.toFixed(0)}%`, detail: "Below return reserve. Delivery eligibility is suspended.", severity: "warn" });
    }
    for (const survivor of survivors) {
      if (survivor.status === "UNSERVED") items.push({ title: `${survivor.survivor_id} unserved`, detail: "No eligible aircraft or kit at the last attempt; retry is automatic while the mission is active.", severity: "warn" });
      if (survivor.status === "UNKNOWN") items.push({ title: `${survivor.survivor_id} delivery unconfirmed`, detail: "Break-beam evidence missing. Never automatically re-fired.", severity: "warn" });
    }
    return items;
  }, [mission.recall, state.safety, vehicles, survivors]);

  const events = (state.events ?? []).filter((event) => !event.topic.startsWith("fleet.telemetry")).slice(-9).reverse();

  const ask = async (value: string) => {
    if (!value.trim()) return;
    setAsking(true);
    try {
      const response = await api.copilot(value.trim());
      setAnswer(response.answer);
    } catch (error) {
      setAnswer(error instanceof Error ? error.message : "Copilot unavailable");
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="mission-screen">
      <header className="mission-header">
        <div className="mission-identity">
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <Badge tone={mission.mode === "REAL" ? "danger" : "accent"}>{mission.mode}</Badge>
            <Badge tone={statusTone(mission.phase)} dot pulse={mission.phase === "EXECUTING"}>{humanise(mission.phase)}</Badge>
            {mission.recall && mission.phase === "EXECUTING" && <Badge tone="danger" dot pulse>Recall active</Badge>}
          </div>
          <div className="name" title={mission.name ?? "Mission"}>{mission.name ?? "No mission loaded"}</div>
        </div>

        <div className="timer-block">
          <div>
            <div className={`timer ${timerTone}`}>{fmtTime(elapsed)}</div>
            <div className="micro">{simElapsed !== null ? `Simulated elapsed · cap ${fmtTime(timeCap)}` : `Mission elapsed · cap ${fmtTime(timeCap)}`}</div>
          </div>
          <ProgressRing value={(elapsed / timeCap) * 100} colour={timerTone === "danger" ? "var(--danger)" : timerTone === "warn" ? "var(--warn)" : "var(--accent)"} />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 190 }}>
          <div className="fleet-dots">
            {vehicles.map((vehicle) => (
              <span key={vehicle.callsign} className={clsx("fleet-dot", vehicle.connected && !vehicle.stale ? "ok" : vehicle.connected ? "warn" : "danger")} title={`${vehicle.callsign} · ${vehicle.connected ? "link up" : "offline"}`}>
                <span className="pip" /> {vehicle.callsign}
              </span>
            ))}
          </div>
          <div className="micro" style={{ color: "var(--faint)" }}>
            {online}/{vehicles.length} linked · {state.video.live_count}/{state.video.total} video ·{" "}
            {state.kits.counts.LOADED ?? 0} kits loaded
          </div>
        </div>

        <div style={{ flex: 1 }} />

        {mission.simulation?.running && (
          <div className="badge warn mono" style={{ height: 28 }}>
            SIM {mission.simulation.speed}× · T+{fmtTime(mission.simulation.sim_time_s)}
          </div>
        )}
        <Badge tone={state.video.recording_count === state.video.total && state.video.total > 0 ? "ok" : "muted"} dot pulse={state.video.recording_count > 0}>
          {state.video.recording_count}/{state.video.total} recording
        </Badge>
        {mission.phase === "COMPLETE" && mission.mission_id && (
          <button className="btn sm" onClick={() => onOpenReplay(mission.mission_id as string)}>
            <ClipboardList size={13} /> Report
          </button>
        )}
      </header>

      <div className="mission-body">
        <div className="mission-col left">
          <Panel title="Fleet" icon={<PlaneTakeoff size={13} />} bodyClass="tight">
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {vehicles.map((vehicle) => (
                <AircraftCard key={vehicle.callsign} vehicle={vehicle} task={activeTask(vehicle.callsign)} />
              ))}
            </div>
          </Panel>

          <Panel title="Shared kit ledger" icon={<Package size={13} />}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 10 }}>
              <MiniStat value={state.kits.counts.LOADED ?? 0} label="Loaded" />
              <MiniStat value={state.kits.counts.RESERVED ?? 0} label="Reserved" />
              <MiniStat value={state.kits.counts.DELIVERED ?? 0} label="Delivered" />
            </div>
            <div className="small muted" style={{ lineHeight: 1.5 }}>
              One atomic reservation per survivor. A missing break-beam becomes UNKNOWN and is never refired.
            </div>
          </Panel>

          <Panel title="Delivery state" icon={<Activity size={13} />} bodyClass="tight">
            {tasks.length === 0 ? (
              <Empty>No deliveries yet.</Empty>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 320, overflow: "auto" }}>
                {[...tasks].reverse().slice(0, 6).map((task) => (
                  <div key={task.assignment_id} style={{ padding: 8, border: "1px solid var(--line)", borderRadius: 7, background: "var(--surface-2)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                      <span className="mono small" style={{ fontWeight: 700 }}>{task.survivor_id}</span>
                      <Badge tone={statusTone(task.state)} style={{ height: 18, fontSize: 8 }}>{humanise(task.state)}</Badge>
                    </div>
                    <div className="allocation-meta" style={{ marginTop: 4 }}>
                      <span>{task.callsign}</span>
                      <span>{task.kit_id}</span>
                      {task.beam_result && <span>beam: {task.beam_result}</span>}
                    </div>
                    <PhaseRail state={task.state} beam={task.beam_result} />
                  </div>
                ))}
              </div>
            )}
            {tasks.length > 0 && (
              <div className="micro" style={{ color: "var(--faint)", lineHeight: 1.5, padding: "7px 8px 2px" }}>
                Rail = delivery sequence. The amber segment at the release step means the kit
                left the magazine with no break-beam evidence — the aircraft moving on is not
                proof of delivery.
              </div>
            )}
          </Panel>

          <Panel
            title="Command lifecycle"
            icon={<Radio size={13} />}
            bodyClass="tight"
            actions={
              <Badge tone={unverifiedTx.length ? "info" : "muted"} style={{ height: 20, fontSize: 8.5 }} title={unverifiedTx.length ? "commands awaiting a vehicle-state check" : "no command is mid-flight on the link"}>
                {unverifiedTx.length} unverified
              </Badge>
            }
          >
            <div className="micro" style={{ color: "var(--faint)", lineHeight: 1.5, marginBottom: 7 }}>
              sent → acknowledged → state verified. An ACK is the autopilot answering, not proof
              that the aircraft did it; only the third segment closes on verification.
            </div>
            {transactions.length === 0 ? (
              <Empty>No command transmitted on this link session.</Empty>
            ) : (
              <div style={{ maxHeight: 260, overflow: "auto" }}>
                {[...transactions].reverse().slice(0, 14).map((tx) => (
                  <div className="tx-row" key={tx.tx_id}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 7, alignItems: "center" }}>
                      <span className="mono" style={{ fontSize: 10, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={`${tx.label} · ${tx.tx_id}`}>
                        {tx.callsign} · {tx.label}
                      </span>
                      <Badge tone={txTone(tx.state)} title={txMeaning(tx.state)} style={{ height: 17, fontSize: 7.5, padding: "0 5px", flexShrink: 0 }}>
                        {humanise(tx.state)}
                      </Badge>
                    </div>
                    <TxRail state={tx.state} />
                    <div className="allocation-meta" style={{ marginTop: 3 }}>
                      <span>{fmtClock(tx.created_at)}</span>
                      <span>cmd {tx.command}</span>
                      {tx.attempts > 1 && <span>{tx.attempts} attempts</span>}
                      {tx.simulated && <span style={{ color: "var(--info)" }}>simulated link</span>}
                      {tx.ack_result !== null && tx.ack_result !== 0 && <span>MAV_RESULT {tx.ack_result}</span>}
                      {tx.expectation === null && ["ACKNOWLEDGED", "VERIFIED"].includes(tx.state) && (
                        <span>no state check defined</span>
                      )}
                    </div>
                    {tx.detail && (
                      <div className="micro" style={{ color: "var(--muted)", lineHeight: 1.4, marginTop: 2 }}>{tx.detail}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
            {transactions.length >= 100 && (
              <div className="micro" style={{ color: "var(--warn)", marginTop: 6 }}>
                Showing the latest 14 of the last 100 transactions. The full command trail is in
                the local evidence log, not this panel.
              </div>
            )}
          </Panel>
        </div>

        <div className="mission-center">
          <TacticalMap
            aoi={mission.aoi}
            plan={mission.plan}
            vehicles={state.fleet.vehicles}
            survivors={survivors}
            safety={state.safety}
            provenance={evidenceQuality(state)}
            onSelectSurvivor={(survivor) => setSelected(survivor)}
            onExpand={() => setMapOpen(true)}
          />
          <div className="video-wall">
            {state.video.feeds.map((feed) => (
              <VideoPanel
                key={feed.callsign}
                feed={feed}
                mode={mission.mode}
                onExpand={() => setVideoOpen(feed.callsign)}
              />
            ))}
          </div>
        </div>

        <div className="mission-col right">
          <Panel
            title={`Survivors · ${state.survivors.total}/10`}
            icon={<MapPin size={13} />}
            actions={
              <div style={{ display: "flex", gap: 4 }}>
                <Badge tone="ok" style={{ height: 20, fontSize: 8.5 }}>{state.survivors.counts.DELIVERED ?? 0} delivered</Badge>
                {state.survivors.counts.UNKNOWN ? <Badge tone="warn" style={{ height: 20, fontSize: 8.5 }}>{state.survivors.counts.UNKNOWN} unknown</Badge> : null}
              </div>
            }
            bodyClass="tight"
          >
            {survivors.length === 0 ? (
              <Empty>No survivor reports yet. Detections require confidence ≥ 0.85 and three frames within 5 m.</Empty>
            ) : (
              <div className="survivor-list">
                {survivors.map((survivor) => (
                  <button
                    key={survivor.survivor_id}
                    className="survivor-row"
                    onClick={() => setSelected(selected?.survivor_id === survivor.survivor_id ? null : survivor)}
                    style={{ background: selected?.survivor_id === survivor.survivor_id ? "var(--surface-2)" : "transparent", border: "none", borderBottom: "1px solid var(--line)", textAlign: "left", width: "100%", color: "inherit" }}
                  >
                    <span className="survivor-id">{survivor.survivor_id.replace("S-", "")}</span>
                    <span className="survivor-main">
                      <span className="survivor-name">
                        {survivor.survivor_id}
                        <Badge tone={statusTone(survivor.status)} style={{ height: 17, fontSize: 7.5, padding: "0 5px" }}>{humanise(survivor.status)}</Badge>
                      </span>
                      <span className="survivor-sub">
                        {survivor.owner ?? survivor.source_aircraft ?? "unassigned"}
                        {survivor.kit_id ? ` · ${survivor.kit_id}` : ""}
                        {survivor.eta_s ? ` · ETA ${fmtTime(survivor.eta_s)}` : ""}
                      </span>
                    </span>
                    <span className="mono tiny" style={{ fontSize: 9, color: "var(--faint)" }}>{survivor.lat.toFixed(4)}<br />{survivor.lon.toFixed(4)}</span>
                  </button>
                ))}
              </div>
            )}
            {selected && (
              <div style={{ marginTop: 8, padding: 9, borderRadius: 7, border: "1px solid var(--line-strong)", background: "var(--surface-2)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <span className="mono small" style={{ fontWeight: 700 }}>{selected.survivor_id}</span>
                  <Badge tone={statusTone(selected.status)}>{humanise(selected.status)}</Badge>
                </div>
                <KV k="Geotag" v={`${selected.lat.toFixed(5)}, ${selected.lon.toFixed(5)}`} />
                <KV k="Drop point" v={`${selected.drop_lat.toFixed(5)}, ${selected.drop_lon.toFixed(5)}`} />
                <KV k="Confidence" v={`${((selected.confidence ?? 0) * 100).toFixed(0)}%`} />
                <KV k="Observations" v={String(selected.observations ?? "—")} />
                <KV k="Evidence" v={selected.data_quality ?? "UNKNOWN"} />
                {selected.note && <div className="small muted" style={{ marginTop: 4 }}>{selected.note}</div>}
              </div>
            )}
          </Panel>

          <Panel title="Allocation" icon={<Crosshair size={13} />} bodyClass="tight">
            {assignments.length === 0 ? (
              <Empty>Awaiting confirmed survivors.</Empty>
            ) : (
              <div style={{ maxHeight: 230, overflow: "auto" }}>
                {[...assignments].reverse().slice(0, 5).map((assignment) => (
                  <AllocationRow key={assignment.assignment_id} assignment={assignment} />
                ))}
              </div>
            )}
            <div className="divider" />
            <div className="micro" style={{ color: "var(--faint)", lineHeight: 1.6 }}>
              Shortest queue-aware ETA · tie-break by more free kits, then lower callsign.
            </div>
          </Panel>

          <Panel title="AI intelligence · advisory" icon={<Bot size={13} />} bodyClass="tight">
            <div style={{ display: "flex", gap: 5, alignItems: "center", marginBottom: 7 }}>
              <Sparkles size={12} style={{ color: "var(--accent)" }} />
              <Badge tone="info" style={{ height: 18, fontSize: 8 }}>Read-only</Badge>
              <span className="micro" style={{ marginLeft: "auto" }}>No command authority</span>
            </div>
            <div className="ai-answer">
              {answer ?? "Ask for a mission summary, allocation rationale or the current risk picture. Answers are generated locally from recorded state."}
            </div>
            <div className="ai-prompts">
              {["Mission risk summary", "Why this allocation?", "What blocks deliveries?"].map((prompt) => (
                <button key={prompt} className="ai-chip" onClick={() => { setQuestion(prompt); void ask(prompt); }} disabled={asking}>
                  {prompt}
                </button>
              ))}
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              <input
                className="input"
                style={{ minHeight: 30, height: 30, fontSize: 11 }}
                placeholder="Ask about this mission…"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && void ask(question)}
              />
              <button className="btn icon sm" onClick={() => void ask(question)} disabled={asking || !question.trim()} aria-label="Send question">
                {asking ? <span className="spin">◌</span> : <Send size={13} />}
              </button>
            </div>
            <div className="micro" style={{ marginTop: 6, color: "var(--faint)" }}>LLM never reaches MAVLink or the release path.</div>
          </Panel>

          <Panel title={`Warnings · ${warnings.length}`} icon={<AlertTriangle size={13} />} bodyClass="tight">
            {warnings.length === 0 ? (
              <div className="small muted" style={{ padding: "6px 0" }}>No active warnings. Unknown states still block release.</div>
            ) : (
              <div style={{ maxHeight: 190, overflow: "auto" }}>
                {warnings.map((warning, index) => (
                  <div key={`${warning.title}-${index}`} className={clsx("warning-row", warning.severity)}>
                    <span className="sev" />
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 10.5 }}>{warning.title}</div>
                      <div className="small muted" style={{ fontSize: 10, lineHeight: 1.4 }}>{warning.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Autonomy timeline" icon={<Clock3 size={13} />} bodyClass="tight">
            <div className="timeline">
              {events.length === 0 ? (
                <Empty>No mission events yet.</Empty>
              ) : (
                events.map((event, index) => (
                  <div className="timeline-item" key={`${event.receive_time}-${index}`}>
                    <span className="timeline-time">{fmtClock(event.receive_time)}</span>
                    <span className={clsx("timeline-dot", statusTone(event.severity))} />
                    <span className="timeline-text"><strong>{event.topic}</strong> · {summarise(event.payload)}</span>
                  </div>
                ))
              )}
            </div>
          </Panel>
        </div>
      </div>

      <Modal open={mapOpen} onClose={() => setMapOpen(false)} title="Tactical map · mission area" width={1180}>
        <div style={{ height: "62vh", minHeight: 420 }}>
          <TacticalMap
            aoi={mission.aoi}
            plan={mission.plan}
            vehicles={state.fleet.vehicles}
            survivors={survivors}
            safety={state.safety}
            provenance={evidenceQuality(state)}
            onSelectSurvivor={setSelected}
          />
        </div>
      </Modal>

      <Modal open={Boolean(videoOpen)} onClose={() => setVideoOpen(null)} title={`Video · ${videoOpen ?? ""}`} width={980}>
        <div style={{ height: "56vh", minHeight: 360 }}>
          <VideoPanel feed={state.video.feeds.find((feed) => feed.callsign === videoOpen)} mode={mission.mode} />
        </div>
      </Modal>
    </div>
  );
}

function MiniStat({ value, label }: { value: number; label: string }) {
  return (
    <div style={{ textAlign: "center", padding: "7px 4px", borderRadius: 6, background: "var(--surface-2)", border: "1px solid var(--line)" }}>
      <div className="mono" style={{ fontSize: 17, fontWeight: 600 }}>{value}</div>
      <div className="micro" style={{ fontSize: 8.5 }}>{label}</div>
    </div>
  );
}

function AircraftCard({ vehicle, task }: { vehicle: Vehicle; task?: DeliveryTask }) {
  const tone = batteryTone(vehicle.battery_pct);
  return (
    <div className={clsx("aircraft-card", vehicle.role === "SEARCH" ? "ins" : "del", !vehicle.connected && "offline")}>
      <div className="aircraft-top">
        <AircraftGlyph role={vehicle.role} colour={vehicle.role === "SEARCH" ? "var(--signal)" : "var(--accent)"} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="aircraft-callsign">{vehicle.callsign}</span>
            <Badge tone={vehicle.connected && !vehicle.stale ? "ok" : vehicle.connected ? "warn" : "danger"} style={{ height: 17, fontSize: 7.5, padding: "0 5px" }}>
              {vehicle.connected ? (vehicle.stale ? "STALE" : "LINK") : "LOST"}
            </Badge>
            {/* Provenance belongs on the aircraft card: a simulated value must never share
                a colour with a heartbeat, whatever the mission mode banner says. */}
            {vehicle.data_quality && vehicle.data_quality !== "LIVE" && (
              <Badge tone={qualityTone(vehicle.data_quality)} style={{ height: 17, fontSize: 7.5, padding: "0 5px" }}
                title={`These readings are ${humanise(vehicle.data_quality)}: ${vehicle.data_quality === "SIMULATED" ? "produced by the engineering simulator, not by this aircraft" : "of unproven origin"}`}>
                {vehicle.data_quality}
              </Badge>
            )}
          </div>
          <div className="aircraft-role">{vehicle.role === "SEARCH" ? "Search · optical survey" : "Delivery · kit magazine"}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="mono" style={{ fontSize: 11, fontWeight: 600 }}>{vehicle.battery_pct !== null ? `${vehicle.battery_pct.toFixed(0)}%` : "—"}</div>
          <div className="micro" style={{ fontSize: 8 }}>battery</div>
        </div>
      </div>
      <div className="battery-row">
        <span className="battery-label">{vehicle.mode !== "—" ? vehicle.mode : "OFFLINE"}</span>
        <Meter value={vehicle.battery_pct} tone={tone} />
      </div>
      <div className="aircraft-metrics">
        <div className="m"><span>AGL</span><span>{vehicle.agl_m !== null ? `${vehicle.agl_m.toFixed(1)} m` : "—"}</span></div>
        <div className="m"><span>GS</span><span>{vehicle.groundspeed_ms !== null ? `${vehicle.groundspeed_ms.toFixed(1)} m/s` : "—"}</span></div>
        <div className="m"><span>Task</span><span>{vehicle.task}</span></div>
        <div className="m"><span>Kits</span><span>{vehicle.role === "DELIVERY" ? vehicle.kits_free : "—"}</span></div>
        {/* Link per-cent is a radio statistic; the heartbeat age is what says whether this
            row is a current truth or a memory. Shown together, never as one number. */}
        <div
          className="m"
          title={vehicle.heartbeat_age_s !== null && vehicle.heartbeat_age_s !== undefined
            ? `Last heartbeat ${vehicle.heartbeat_age_s.toFixed(1)} s ago · RSSI ${vehicle.rssi ?? "—"} dBm`
            : "No heartbeat received on this station"}
        >
          <span>Link</span>
          <span>
            {vehicle.link_quality !== null ? `${vehicle.link_quality}%` : "—"}
            {vehicle.heartbeat_age_s !== null && vehicle.heartbeat_age_s !== undefined
              ? ` · hb ${vehicle.heartbeat_age_s.toFixed(1)}s`
              : ""}
          </span>
        </div>
        <div className="m"><span>GPS</span><span>{vehicle.gps_fix >= 3 ? `${vehicle.gps_sats} sats` : "no fix"}</span></div>
      </div>
      {task && (
        <div style={{ marginTop: 8, paddingTop: 7, borderTop: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 6 }}>
          <CircleDot size={10} style={{ color: "var(--accent)" }} />
          <span className="small" style={{ fontWeight: 700 }}>{task.survivor_id}</span>
          <span className="micro">{humanise(task.state)}</span>
          <span className="mono micro" style={{ marginLeft: "auto", color: "var(--faint)" }}>{task.kit_id}</span>
        </div>
      )}
    </div>
  );
}

function PhaseRail({ state, beam }: { state: string; beam?: string | null }) {
  const s = (state || "").toUpperCase();
  const aborted = s === "ABORTED";
  const index = SPINE_SLOT[s] ?? -1;
  // The release slot reports what the break-beam actually said, not what the machine hoped
  // for: a task that has already climbed away with no beam evidence still shows an amber
  // release segment, because "the aircraft moved on" is not "the kit was delivered".
  const releaseProven = s === "CONFIRMED" || beam === "CONFIRMED";
  const label = aborted
    ? "Delivery aborted · off the normal sequence"
    : index < 0
      ? `Phase ${s || "unknown"} is not in the recorded sequence`
      : `${humanise(s)} · step ${index + 1} of ${DELIVERY_SPINE.length}`;
  return (
    <div
      className="phase-rail"
      role="img"
      aria-label={`Delivery progress: ${label}. Segments: ${DELIVERY_SPINE.map(humanise).join(", ")}.`}
      title={label}
    >
      {DELIVERY_SPINE.map((phase, i) => {
        const reached = index >= 0 && i <= index;
        let colour = "var(--line-strong)";
        if (aborted) colour = i === 0 ? "var(--danger)" : "var(--line-strong)";
        else if (phase === "CONFIRMED" && reached) colour = releaseProven ? "var(--ok)" : "var(--warn)";
        else if (reached) colour = "var(--accent)";
        return (
          <span
            key={phase}
            title={`${humanise(phase)}${phase === "CONFIRMED" && reached ? (releaseProven ? " · break-beam confirmed" : " · no delivery evidence") : ""}`}
            style={{ background: colour }}
          />
        );
      })}
    </div>
  );
}

/**
 * Command lifecycle for one transmitted MAVLink command: sent → acknowledged → vehicle state
 * verified. The third segment stays open on an ACK, because an autopilot acceptance is proof
 * that the command was received, not proof that the aircraft did it.
 */
function TxRail({ state }: { state: string }) {
  const { stage, outcome } = txStage(state);
  const colour = outcome === "ok" ? "var(--ok)" : outcome === "fail" ? "var(--danger)" : outcome === "warn" ? "var(--warn)" : "var(--accent)";
  return (
    <div className="phase-rail" role="img" aria-label={`Command lifecycle: ${humanise(state)} — ${txMeaning(state)}`} title={txMeaning(state)}>
      {TX_STEPS.map((step, i) => (
        <span
          key={step}
          title={`${humanise(step)}${step === "verified" && stage < 3 ? " · not reached" : ""}`}
          style={{
            background: i < stage ? colour : "var(--line-strong)",
            opacity: i === stage - 1 && outcome === "open" && stage < 3 ? 0.6 : 1,
          }}
        />
      ))}
    </div>
  );
}

function AllocationRow({ assignment }: { assignment: Assignment }) {
  const decision = assignment.decision;
  return (
    <div className="allocation-row">
      <div className="allocation-top">
        <span className="mono small" style={{ fontWeight: 700 }}>{assignment.survivor_id}</span>
        <span className="mono tiny" style={{ color: "var(--faint)", fontSize: 9 }}>{assignment.assignment_id}</span>
        <Badge tone={statusTone(assignment.status)} style={{ height: 17, fontSize: 7.5 }}>{humanise(assignment.status)}</Badge>
      </div>
      <div className="allocation-meta">
        <span>{assignment.callsign}</span>
        <span>{assignment.kit_id}</span>
        <span>ETA {fmtTime(assignment.eta_s)}</span>
        {decision?.candidates?.length ? <span>{decision.candidates.length} candidate{decision.candidates.length > 1 ? "s" : ""}</span> : null}
      </div>
    </div>
  );
}

function summarise(payload: Record<string, unknown>): string {
  const candidate =
    (payload.detail as string) ||
    (payload.phase as string) ||
    (payload.state as string) ||
    (payload.reason as string) ||
    (payload.result as string);
  if (candidate) return String(candidate).slice(0, 90);
  if (payload.survivor && typeof payload.survivor === "object") {
    const survivor = payload.survivor as { survivor_id?: string; status?: string };
    return `${survivor.survivor_id ?? "survivor"} · ${survivor.status ?? ""}`;
  }
  if (payload.assignment && typeof payload.assignment === "object") {
    const assignment = payload.assignment as { survivor_id?: string; callsign?: string; kit_id?: string };
    return `${assignment.survivor_id ?? "?"} → ${assignment.callsign ?? "?"} · ${assignment.kit_id ?? ""}`;
  }
  return "recorded";
}
