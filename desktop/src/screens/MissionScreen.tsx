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
  Send,
  Sparkles,
} from "lucide-react";
import clsx from "clsx";
import { api } from "../lib/api";
import { batteryTone, fmtClock, fmtDistance, fmtTime, humanise, statusTone } from "../lib/format";
import type { Assignment, DeliveryTask, Survivor, Vehicle } from "../lib/types";
import { AircraftGlyph, Badge, Empty, KV, Meter, Modal, Panel, ProgressRing } from "../components/ui";
import { TacticalMap } from "../components/TacticalMap";
import { VideoPanel } from "../components/VideoPanel";
import { useGcs } from "../state/GcsContext";

const PHASE_ORDER = ["QUEUED", "LAUNCHING", "TRANSIT", "HOLD_CLEARANCE", "DESCENT", "HOVER_VERIFY", "RELEASE_SENT", "CLIMB", "RETURNING", "LANDING", "STANDBY"];

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
  const timeCap = 30 * 60;
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
            <div className="micro">{simElapsed !== null ? "Simulated elapsed · cap 30:00" : "Mission elapsed · cap 30:00"}</div>
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
            {online}/3 linked · {state.video.live_count}/3 video ·{" "}
            {state.kits.counts.LOADED ?? 0} kits loaded
          </div>
        </div>

        <div style={{ flex: 1 }} />

        {mission.simulation?.running && (
          <div className="badge warn mono" style={{ height: 28 }}>
            SIM {mission.simulation.speed}× · T+{fmtTime(mission.simulation.sim_time_s)}
          </div>
        )}
        <Badge tone={state.video.recording_count === 3 ? "ok" : "muted"} dot pulse={state.video.recording_count > 0}>
          {state.video.recording_count}/3 recording
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
                    <PhaseRail state={task.state} />
                  </div>
                ))}
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
                <KV k="Evidence" v={selected.data_quality ?? "SIMULATED"} />
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
          </div>
          <div className="aircraft-role">{vehicle.role === "SEARCH" ? "Search · YOLOv8n" : "Delivery · 5-kit magazine"}</div>
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
        <div className="m"><span>Link</span><span>{vehicle.link_quality !== null ? `${vehicle.link_quality}%` : "—"}</span></div>
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

function PhaseRail({ state }: { state: string }) {
  const index = PHASE_ORDER.indexOf(state);
  return (
    <div style={{ display: "flex", gap: 2, marginTop: 6 }}>
      {PHASE_ORDER.slice(0, 9).map((phase, i) => (
        <span
          key={phase}
          title={humanise(phase)}
          style={{
            flex: 1,
            height: 2,
            borderRadius: 2,
            background: index >= i && index >= 0 ? "var(--accent)" : "var(--line-strong)",
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
