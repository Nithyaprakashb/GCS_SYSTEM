import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  CircleDashed,
  Cpu,
  FileUp,
  Gauge,
  HardDrive,
  Link2,
  Loader2,
  Map as MapIcon,
  PlaneTakeoff,
  Radio,
  RotateCcw,
  ScanLine,
  ShieldAlert,
  ShieldCheck,
  UploadCloud,
  Video,
  X,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api } from "../lib/api";
import type { MissionPlan, MissionStatus, UploadReceipt } from "../lib/types";
import { evidenceQuality, fmtDistance, fmtNumber, humanise, qualityTone, statusTone } from "../lib/format";
import { Badge, Modal, Notice, Panel } from "../components/ui";
import { TacticalMap } from "../components/TacticalMap";
import { useGcs } from "../state/GcsContext";

interface Props {
  onMission: () => void;
}

interface Diagnostic {
  check_id: string;
  title: string;
  status: string;
  detail: string;
}

/*
 * Provenance comes from `evidenceQuality` in ../lib/format, shared with the mission screen:
 * a REAL session with no heartbeats has no evidence, and a mixed fleet (one aircraft linked,
 * two lost) must not be reported as LIVE.
 */

const STAGES = [
  { id: 0, title: "System & links", sub: "Initialise GCS, telemetry and video" },
  { id: 1, title: "Search area", sub: "Import the mission boundary" },
  { id: 2, title: "Flight plan", sub: "Generate and validate the search plan" },
  { id: 3, title: "Inspection", sub: "Hardware, feeds and manual sign-offs" },
  { id: 4, title: "Preflight & upload", sub: "Verify aircraft and mission read-back" },
  { id: 5, title: "Launch authority", sub: "Readiness gate and mission start" },
];

export function SetupScreen({ onMission }: Props) {
  const { state, run, busy, connected } = useGcs();
  const [stage, setStage] = useState(0);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [report, setReport] = useState<import("../lib/types").PreflightReport | null>(null);
  const [gate, setGate] = useState<Record<string, unknown> | null>(null);
  const [startOpen, setStartOpen] = useState(false);
  const [startToken, setStartToken] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const mission = state?.mission;
  const fleet = state?.fleet;
  const vehicles = fleet?.vehicles;
  const video = state?.video;
  const manual = state?.preflight.manual ?? [];
  const allSigned = manual.length > 0 && manual.every((item) => item.signed);
  const sim = mission?.mode === "SIMULATION";
  const finished = mission?.phase === "COMPLETE" || mission?.phase === "ABORTED";
  const connectedCount = useMemo(() => {
    if (!vehicles) return 0;
    const list = Array.isArray(vehicles) ? vehicles : Object.values(vehicles);
    return list.filter((vehicle) => vehicle.connected).length;
  }, [vehicles]);

  const refreshDiagnostics = useCallback(async () => {
    try {
      const result = (await api.diagnostics()) as { checks?: Diagnostic[] };
      setDiagnostics(result.checks ?? []);
    } catch {
      setDiagnostics([]);
    }
  }, []);

  useEffect(() => {
    void refreshDiagnostics();
    const timer = window.setInterval(() => void refreshDiagnostics(), 6000);
    return () => window.clearInterval(timer);
  }, [refreshDiagnostics]);

  const completed = useMemo(() => {
    return {
      0: connected && connectedCount === 3 && (video?.live_count ?? 0) === 3,
      1: Boolean(mission?.aoi),
      2: Boolean(mission?.plan),
      3: allSigned,
      4: Boolean(mission?.upload_verified) || ["READY", "EXECUTING", "COMPLETE"].includes(mission?.phase ?? ""),
      5: ["EXECUTING", "COMPLETE"].includes(mission?.phase ?? ""),
    } as Record<number, boolean>;
  }, [connected, connectedCount, video?.live_count, mission?.aoi, mission?.plan, mission?.upload_verified, mission?.phase, allSigned]);

  const connectFleet = async (mode: "SIMULATION" | "REAL") => {
    setLocalError(null);
    await run(`Connect fleet · ${mode}`, () => api.connect(mode), `Fleet connected in ${mode}`);
    await refreshDiagnostics();
  };

  const loadSample = async () => {
    const result = await run("Load sample boundary", () => api.loadSample(), "Sample 10 ha boundary imported");
    if (result) setStage(2);
  };

  const importFile = async (file: File) => {
    if (file.size > 5 * 1024 * 1024) {
      setLocalError("KML must be smaller than 5 MB");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".kml")) {
      setLocalError("Only .kml files are accepted");
      return;
    }
    const text = await file.text();
    const result = await run("Import KML", () => api.importKml({ kml_text: text, filename: file.name }), "Boundary imported");
    if (result) setStage(2);
  };

  const generatePlan = async () => {
    await run("Generate plan", () => api.plan(), "Deterministic lawnmower plan generated");
  };

  const runPreflight = async () => {
    const result = await run("Run preflight", () => api.preflight(), "Preflight complete");
    if (result) {
      setReport(result);
      setStage(4);
    }
  };

  const upload = async () => {
    setUploading(true);
    try {
      const result = await run("Upload mission", () => api.upload(), "Mission uploaded and read back");
      if (result) {
        const gateResult = await api.gate();
        setGate(gateResult);
        const pre = await api.preflight();
        setReport(pre);
      }
    } finally {
      setUploading(false);
    }
  };

  const evaluateGate = async () => {
    try {
      const result = await api.gate();
      setGate(result);
      const pre = await api.preflight();
      setReport(pre);
      const ready = Boolean(result.ready);
      if (ready) setStage(5);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "Gate evaluation failed");
    }
  };

  const startMission = async () => {
    const result = await run("Start mission", () => api.start("START"), "Mission started");
    if (result) {
      setStartOpen(false);
      setStartToken("");
      onMission();
    }
  };

  const startNewMission = async () => {
    setLocalError(null);
    const result = await run("Start new mission", () => api.newMission(), "Session cleared — import the next boundary");
    if (result !== null) {
      setStage(1);
      setReport(null);
      setGate(null);
      setStartOpen(false);
      setStartToken("");
    }
  };

  if (!state) {
    return (
      <div className="page" style={{ display: "grid", placeItems: "center" }}>
        <Loader2 className="spin" />
      </div>
    );
  }

  return (
    <div className="page" style={{ overflow: "hidden" }}>
      <div className="setup-grid">
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
          <Panel title="Mission setup" icon={<Cpu size={13} />} flush>
            <div className="stage-list">
              {STAGES.map((item) => (
                <button
                  key={item.id}
                  className={clsx("stage-item", stage === item.id && "active", completed[item.id] && "done")}
                  onClick={() => setStage(item.id)}
                >
                  <span className="stage-index">
                    {completed[item.id] ? <Check size={12} /> : String(item.id + 1).padStart(2, "0")}
                  </span>
                  <span>
                    <div className="stage-name">{item.title}</div>
                    <div className="stage-status">{completed[item.id] ? "Complete" : item.sub}</div>
                  </span>
                </button>
              ))}
            </div>
          </Panel>
          <Panel title="Operating mode" icon={<ShieldCheck size={13} />}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <Badge tone={mission?.mode === "REAL" ? "danger" : "accent"} dot pulse>
                {mission?.mode ?? "SIMULATION"}
              </Badge>
              <span className="mono small muted">{state.app.version}</span>
            </div>
            <div className="divider" />
            <div className="kv">
              <span className="k">Telemetry</span>
              <span className="v">{connected ? `${connectedCount}/3 online` : "Disconnected"}</span>
            </div>
            <div className="kv">
              <span className="k">Video</span>
              <span className="v">{video?.live_count ?? 0}/3 live</span>
            </div>
            <div className="kv">
              <span className="k">Kits loaded</span>
              <span className="v">{state.kits?.counts?.LOADED ?? 0}/10</span>
            </div>
          </Panel>
          <Panel title="Integrity posture" icon={<HardDrive size={13} />}>
            <div className="small ink2" style={{ lineHeight: 1.6 }}>
              Local-only operation. No cloud account, internet tile fetch or online model is used in mission mode.
            </div>
            <div className="divider" />
            <div className="micro" style={{ lineHeight: 1.7, color: "var(--faint)" }}>
              Every operator action is written to the mission event log with actor identity.
            </div>
          </Panel>
        </div>

        <div className="setup-content">
          {localError && (
            <Notice tone="danger" icon={<XCircle size={14} />}>
              {localError}
            </Notice>
          )}
          {!connected && (
            <Notice tone="warn" icon={<ShieldAlert size={14} />}>
              Backend connection lost. Setup actions are disabled until the local service is reachable.
            </Notice>
          )}
          {finished && (
            <Notice tone="muted" icon={<RotateCcw size={14} />}>
              <span>
                Mission {mission?.mission_id} is {humanise(mission?.phase)}. Its evidence is archived; the launch
                stages stay locked until a new session is started.
              </span>
              <button className="btn sm" style={{ marginTop: 7 }} onClick={() => void startNewMission()} disabled={busy !== null}>
                <RotateCcw size={12} /> Start new mission
              </button>
            </Notice>
          )}

          {stage === 0 && (
            <SystemStage
              mode={mission?.mode ?? "SIMULATION"}
              diagnostics={diagnostics}
              connected={connected}
              connectedCount={connectedCount}
              videoLive={video?.live_count ?? 0}
              busy={busy}
              onConnect={connectFleet}
              onVideo={() => void run("Initialise video", () => api.videoStart(mission?.mode), "Video service started")}
              onContinue={() => setStage(1)}
              topologyVerified={Boolean(fleet?.topology_commissioned)}
            />
          )}

          {stage === 1 && (
            <AreaStage
              aoi={mission?.aoi ?? null}
              busy={busy}
              dragging={dragging}
              setDragging={setDragging}
              fileInput={fileInput}
              onSample={loadSample}
              onFile={importFile}
              plan={mission?.plan ?? null}
              vehicles={vehicles}
              limits={mission?.limits}
              provenance={evidenceQuality(state)}
              onBack={() => setStage(0)}
              onContinue={() => setStage(2)}
            />
          )}

          {stage === 2 && (
            <PlanStage
              aoi={mission?.aoi ?? null}
              plan={(mission?.plan as MissionPlan | null) ?? null}
              vehicles={vehicles}
              busy={busy}
              provenance={evidenceQuality(state)}
              onGenerate={generatePlan}
              onBack={() => setStage(1)}
              onContinue={() => setStage(3)}
            />
          )}

          {stage === 3 && (
            <InspectionStage
              state={state}
              busy={busy}
              onSign={(id, signed) => void run("Sign check", () => api.sign(id, signed), signed ? "Check signed" : "Signature removed")}
              onSignAll={() => void run("Sign all (simulation)", () => api.signAllSimulation(), "Manual checks signed for simulation")}
              onPreflight={runPreflight}
              onBack={() => setStage(2)}
            />
          )}

          {stage === 4 && (
            <PreflightStage
              state={state}
              report={report ?? state.preflight.last}
              gate={gate}
              busy={busy}
              uploading={uploading}
              onRun={runPreflight}
              onUpload={upload}
              onGate={evaluateGate}
              onBack={() => setStage(3)}
              onContinue={() => setStage(5)}
            />
          )}

          {stage === 5 && (
            <LaunchStage
              state={state}
              gate={gate}
              report={report ?? state.preflight.last}
              busy={busy}
              onGate={evaluateGate}
              onStart={() => setStartOpen(true)}
              onBack={() => setStage(4)}
              onMission={onMission}
            />
          )}
        </div>
      </div>

      <Modal
        open={startOpen}
        title="Authorise autonomous launch"
        onClose={() => setStartOpen(false)}
        footer={
          <>
            <button className="btn ghost" onClick={() => setStartOpen(false)}>Cancel</button>
            <button className="btn primary" disabled={startToken !== "START" || busy !== null} onClick={() => void startMission()}>
              <PlaneTakeoff size={14} /> Launch mission
            </button>
          </>
        }
      >
        <p className="modal-text">
          This starts autonomous search and authorises the deterministic delivery state machine to release kits
          when all safety interlocks pass. Keep the safety pilot in control and the recall path clear.
        </p>
        <div className="field">
          <label>Type START to confirm</label>
          <input className="input mono" value={startToken} onChange={(event) => setStartToken(event.target.value.toUpperCase())} placeholder="START" autoFocus />
        </div>
        <div style={{ marginTop: 12 }}>
          <Notice tone={sim ? "warn" : "danger"} icon={<ShieldAlert size={14} />}>
            {sim
              ? "SIMULATION: all aircraft, detections, video and deliveries are synthetic and labelled."
              : "REAL: commands will be sent to physical aircraft. Verify airspace, personnel and onboard failsafes."}
          </Notice>
        </div>
      </Modal>
    </div>
  );
}

function SystemStage({
  mode,
  diagnostics,
  connected,
  connectedCount,
  videoLive,
  busy,
  onConnect,
  onVideo,
  onContinue,
  topologyVerified,
}: {
  mode: string;
  diagnostics: Diagnostic[];
  connected: boolean;
  connectedCount: number;
  videoLive: number;
  busy: string | null;
  onConnect: (mode: "SIMULATION" | "REAL") => void;
  onVideo: () => void;
  onContinue: () => void;
  topologyVerified: boolean;
}) {
  const [selected, setSelected] = useState<"SIMULATION" | "REAL">(mode === "REAL" ? "REAL" : "SIMULATION");
  /*
   * The safety engine reports itself through the startup diagnostics. Showing a fixed
   * green "ARMED" here would claim a protection state the station has not measured, so
   * the card reflects what the backend actually answered — including "UNKNOWN" when it
   * has not answered yet.
   */
  const safetyCheck = diagnostics.find((check) => check.check_id === "safety");
  const safetyTone =
    safetyCheck?.status === "PASS" ? "var(--ok)"
      : safetyCheck?.status === "WARN" ? "var(--warn)"
        : safetyCheck?.status === "FAIL" ? "var(--danger)"
          : "var(--muted)";
  const safetyLabel =
    safetyCheck?.status === "PASS" ? "RUNNING"
      : safetyCheck?.status === "WARN" ? "CHECK"
        : safetyCheck?.status === "FAIL" ? "FAULTED"
          : "UNKNOWN";
  return (
    <>
      <div className="setup-heading">
        <div>
          <div className="micro">Stage 01 · System initialisation</div>
          <h1>Establish the ground station</h1>
          <p>
            Bind the deterministic backend, choose the operating mode, then discover the three aircraft and
            bring up all analog video channels. Identity comes from valid MAVLink heartbeats, never from a USB port.
          </p>
        </div>
        <Badge tone={connected ? "ok" : "danger"} dot pulse={connected}>
          {connected ? "Backend local" : "Backend offline"}
        </Badge>
      </div>

      <div className="mode-choice">
        <button className={clsx("mode-card", selected === "SIMULATION" && "active")} onClick={() => setSelected("SIMULATION")}>
          <Badge tone="accent">Engineering simulator</Badge>
          <h3>Simulation</h3>
          <p>Deterministic three-aircraft mission with synthetic detections, watermarked video and injectable faults. No RF or flight hardware is commanded.</p>
        </button>
        <button className={clsx("mode-card", selected === "REAL" && "active")} onClick={() => setSelected("REAL")}>
          <Badge tone="danger">Physical aircraft</Badge>
          <h3>Real operations</h3>
          <p>Commands physical aircraft over RFD868x MAVLink. Requires commissioned links, validated SYSIDs and a safety pilot. Unverified links remain blocked in preflight.</p>
        </button>
      </div>

      {selected === "REAL" && !topologyVerified && (
        <Notice tone="danger" icon={<ShieldAlert size={14} />}>
          Telemetry topology is not commissioned for this deployment. A detected serial port is not a validated
          aircraft link. Real-mode readiness will remain blocked until the RFD868x topology is physically verified.
        </Notice>
      )}

      <div className="setup-cards">
        <div className="metric-card">
          <div className="micro">Aircraft discovered</div>
          <div className="value">{connectedCount}/3</div>
          <div className="sub">{connected ? "MAVLink heartbeats validated" : "Connect to begin discovery"}</div>
        </div>
        <div className="metric-card">
          <div className="micro">Video channels</div>
          <div className="value">{videoLive}/3</div>
          <div className="sub">TX800 receivers · recorded independently</div>
        </div>
        <div className="metric-card">
          <div className="micro">Safety engine</div>
          <div className="value" style={{ color: safetyTone }}>{safetyLabel}</div>
          <div className="sub">{safetyCheck?.detail ?? "Deterministic engine has not reported yet"}</div>
        </div>
      </div>

      <Panel title="Startup diagnostics" icon={<Cpu size={13} />}>
        {diagnostics.length === 0 ? (
          <div className="empty"><Loader2 className="spin" size={16} /> Reading local diagnostics</div>
        ) : (
          diagnostics.map((check) => (
            <div className="checkline" key={check.check_id}>
              <span className={clsx("check-icon", check.status === "PASS" ? "ok" : check.status === "WARN" ? "warn" : "fail")}>
                {check.status === "PASS" ? <Check size={11} /> : check.status === "WARN" ? <ShieldAlert size={11} /> : <X size={11} />}
              </span>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                  <span style={{ fontWeight: 700, fontSize: 12 }}>{check.title}</span>
                  <span className={clsx("badge", check.status === "PASS" ? "ok" : check.status === "WARN" ? "warn" : "danger")} style={{ height: 19, fontSize: 8.5 }}>
                    {check.status}
                  </span>
                </div>
                <div className="small muted" style={{ marginTop: 2 }}>{check.detail}</div>
              </div>
            </div>
          ))
        )}
      </Panel>

      <div className="stepper-footer">
        <div className="small muted">Local time {new Date().toLocaleTimeString("en-IN", { hour12: false })} · no external services</div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={onVideo} disabled={busy !== null}>
            <Video size={14} /> Check video
          </button>
          <button className="btn primary" onClick={() => onConnect(selected)} disabled={busy !== null || !connected}>
            <Link2 size={14} /> Connect fleet
          </button>
          <button className="btn" onClick={onContinue} disabled={!connected}>
            Continue <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </>
  );
}

function AreaStage({
  aoi,
  busy,
  dragging,
  setDragging,
  fileInput,
  onSample,
  onFile,
  plan,
  vehicles,
  limits,
  provenance,
  onBack,
  onContinue,
}: {
  aoi: import("../lib/types").Aoi | null;
  busy: string | null;
  dragging: boolean;
  setDragging: (value: boolean) => void;
  fileInput: React.RefObject<HTMLInputElement | null>;
  onSample: () => void;
  onFile: (file: File) => void;
  plan: MissionPlan | null;
  vehicles: import("../lib/types").Vehicle[] | Record<string, import("../lib/types").Vehicle> | undefined;
  limits: MissionStatus["limits"];
  provenance: string;
  onBack: () => void;
  onContinue: () => void;
}) {
  /*
   * Every number below is read from the plan the backend just computed or from the limits
   * the safety rules actually consult. A hard-coded "12 m AGL" here would keep advertising
   * the old profile after someone changed it, and an operator would launch believing a
   * clearance floor the station is not enforcing.
   */
  const inputs = plan?.estimate?.inputs;
  const profile: { k: string; v: string | null; sub?: string }[] = [
    {
      k: "Search altitude",
      v: inputs?.search_altitude_m !== undefined ? `${fmtNumber(inputs.search_altitude_m, 1)} m AGL` : null,
    },
    { k: "Search speed", v: inputs?.search_speed_ms !== undefined ? `${fmtNumber(inputs.search_speed_ms, 1)} m/s` : null },
    { k: "Survivor cap", v: limits?.max_survivors !== undefined ? String(limits.max_survivors) : null },
    { k: "Merge radius", v: limits?.merge_distance_m !== undefined ? `${fmtNumber(limits.merge_distance_m, 1)} m` : null },
    {
      k: "Release clearance",
      v: limits?.descent_clearance_m !== undefined ? `${fmtNumber(limits.descent_clearance_m, 1)} m` : null,
    },
  ];

  return (
    <>
      <div className="setup-heading">
        <div>
          <div className="micro">Stage 02 · Search area & mission configuration</div>
          <h1>Define the search boundary</h1>
          <p>Import the authoritative KML polygon. Geometry is validated locally, holes are handled explicitly, and the original file hash is retained as evidence.</p>
        </div>
        {aoi && <Badge tone="ok" dot>Boundary valid</Badge>}
      </div>

      {!aoi ? (
        <div
          className={clsx("dropzone", dragging && "drag")}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files?.[0];
            if (file) onFile(file);
          }}
          onClick={() => fileInput.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => event.key === "Enter" && fileInput.current?.click()}
        >
          <FileUp size={26} style={{ color: "var(--accent)" }} />
          <h3>Drop a .kml file or click to browse</h3>
          <p>Maximum 5 MB · parsed offline · no coordinate rounding beyond 7 decimals</p>
          <input
            ref={fileInput}
            type="file"
            accept=".kml,application/vnd.google-earth.kml+xml"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onFile(file);
              event.target.value = "";
            }}
          />
          <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 16 }}>
            <button
              className="btn"
              onClick={(event) => {
                event.stopPropagation();
                onSample();
              }}
              disabled={busy !== null}
            >
              Use 10 ha sample zone
            </button>
          </div>
        </div>
      ) : (
        <div className="setup-cards" style={{ gridTemplateColumns: "minmax(0, 1fr) 280px" }}>
          <TacticalMap aoi={aoi} plan={plan} vehicles={vehicles} provenance={provenance} className="" />
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <Panel title="Boundary" icon={<MapIcon size={13} />}>
              <div className="kv"><span className="k">Area</span><span className="v mono">{aoi.area_ha.toFixed(3)} ha</span></div>
              <div className="kv"><span className="k">Perimeter</span><span className="v mono">{fmtDistance(aoi.perimeter_m)}</span></div>
              <div className="kv"><span className="k">Vertices</span><span className="v mono">{aoi.ring.length}</span></div>
              <div className="kv"><span className="k">Source hash</span><span className="v mono" title={aoi.source_hash}>{aoi.source_hash?.slice(0, 12)}…</span></div>
            </Panel>
            <Panel title="Mission profile" icon={<Gauge size={13} />}>
              {profile.map((row) => (
                <div className="kv" key={row.k}>
                  <span className="k">{row.k}</span>
                  <span className="v mono">{row.v ?? "—"}</span>
                </div>
              ))}
              <div className="divider" />
              <div className="small muted" style={{ lineHeight: 1.5 }}>
                {plan
                  ? "Read from the generated plan and the fleet profile limits the safety rules consult."
                  : "Values appear once a plan is generated; the station does not advertise limits it has not computed."}
              </div>
            </Panel>
            <button className="btn" onClick={onSample} disabled={busy !== null}>Replace with sample</button>
          </div>
        </div>
      )}

      {aoi?.warnings?.length ? (
        <Notice tone="warn" icon={<ShieldAlert size={14} />}>
          {aoi.warnings.join(" · ")}
        </Notice>
      ) : null}

      <div className="stepper-footer">
        <button className="btn ghost" onClick={onBack}><ArrowLeft size={14} /> Back</button>
        <button className="btn primary" onClick={onContinue} disabled={!aoi}>
          Continue to plan <ArrowRight size={14} />
        </button>
      </div>
    </>
  );
}

function PlanStage({
  aoi,
  plan,
  vehicles,
  busy,
  provenance,
  onGenerate,
  onBack,
  onContinue,
}: {
  aoi: import("../lib/types").Aoi | null;
  plan: MissionPlan | null;
  vehicles: import("../lib/types").Vehicle[] | Record<string, import("../lib/types").Vehicle> | undefined;
  busy: string | null;
  provenance: string;
  onGenerate: () => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const inputs = plan?.estimate?.inputs;
  const speed = inputs?.search_speed_ms;
  const fences = Object.values(plan?.geofences ?? {});
  const ceilings = fences.map((fence) => fence.max_alt_m).filter((value) => typeof value === "number") as number[];
  const inset = inputs?.lane_inset_m ?? plan?.search?.lane_inset_m;
  return (
    <>
      <div className="setup-heading">
        <div>
          <div className="micro">Stage 03 · Deterministic flight plan</div>
          <h1>Generate the lawnmower search</h1>
          <p>Fixed heading along the minimum rotated rectangle, with lane ends inset from the boundary. Overlap, inset, altitude and speed are read from the fleet profile — the values actually used are shown on this plan, never quoted from a default.</p>
        </div>
        {plan && <Badge tone={plan.validation?.ok ? "ok" : "danger"} dot>{plan.validation?.ok ? "Validated" : "Invalid"}</Badge>}
      </div>

      {!plan ? (
        <Panel title="Plan generation" icon={<ScanLine size={13} />}>
          <div className="empty" style={{ padding: 34 }}>
            <ScanLine size={26} style={{ color: "var(--accent)" }} />
            <div style={{ maxWidth: 420, lineHeight: 1.6 }}>
              The planner derives lane spacing from the camera swath at the profiled search altitude, then fits the
              minimum number of complete lanes inside the boundary. No manual waypoint editing is required.
            </div>
            <button className="btn primary" onClick={onGenerate} disabled={busy !== null || !aoi} style={{ marginTop: 6 }}>
              Generate plan
            </button>
          </div>
        </Panel>
      ) : (
        <>
          <div className="setup-cards">
            <div className="metric-card"><div className="micro">Search lanes</div><div className="value">{plan.search.lane_count}</div><div className="sub">{fmtNumber(plan.search.lane_spacing_m, 2)} m spacing · {fmtNumber(plan.search.usable_swath_m, 1)} m swath</div></div>
            <div className="metric-card"><div className="micro">Path length</div><div className="value">{fmtNumber(plan.search.path_length_m / 1000, 2)}<span style={{ fontSize: 13, color: "var(--muted)" }}> km</span></div><div className="sub">Serpentine, fixed heading</div></div>
            <div className="metric-card"><div className="micro">Search duration</div><div className="value">{fmtNumber((plan.search.search_time_s ?? 0) / 60, 1)}<span style={{ fontSize: 13, color: "var(--muted)" }}> min</span></div><div className="sub">{speed !== undefined ? `At ${fmtNumber(speed, 1)} m/s ground speed` : "Speed not published by the plan"}</div></div>
            <div className="metric-card"><div className="micro">Mission estimate</div><div className="value">{plan.estimate?.total_min_low ?? "—"}–{plan.estimate?.total_min_high ?? "—"}<span style={{ fontSize: 13, color: "var(--muted)" }}> min</span></div><div className="sub">CALCULATED · includes delivery allowance</div></div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 300px", gap: 12 }}>
            <TacticalMap aoi={aoi} plan={plan} vehicles={vehicles} provenance={provenance} />
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <Panel title="Geofences" icon={<ShieldCheck size={13} />}>
                {fences.map((fence) => (
                  <div className="kv" key={fence.callsign}>
                    <span className="k mono">{fence.callsign}</span>
                    <span className="v">
                      {String(fence.type ?? "inclusion")}
                      {typeof fence.max_alt_m === "number" ? ` · ${fmtNumber(fence.max_alt_m, 1)} m ceil` : ""}
                    </span>
                  </div>
                ))}
                <div className="divider" />
                <div className="small muted">
                  {fences.length
                    ? `${fences[0].action ?? "RTL"} after search · ${ceilings.length ? `ceiling ${fmtNumber(Math.max(...ceilings), 1)} m` : "ceiling not published"}${inset !== undefined ? ` · ${fmtNumber(inset, 1)} m lane inset` : ""}`
                    : "No fences in this plan."}
                </div>
              </Panel>
              <Panel title="Energy" icon={<Gauge size={13} />}>
                {Object.entries(plan.energy ?? {}).slice(0, 4).map(([key, value]) => (
                  <div className="kv" key={key}><span className="k">{humanise(key)}</span><span className="v mono">{typeof value === "number" ? value.toFixed(1) : String(value)}</span></div>
                ))}
              </Panel>
              <Panel title="Validation" icon={<CheckCircle2 size={13} />}>
                <div className="checkline" style={{ padding: "6px 0" }}>
                  <span className={clsx("check-icon", plan.validation?.ok ? "ok" : "fail")}>{plan.validation?.ok ? <Check size={11} /> : <X size={11} />}</span>
                  <div className="small">{plan.validation?.ok ? "Geometry and fence checks passed" : (plan.validation?.errors ?? []).join(" · ")}</div>
                </div>
                <div className="mono micro" style={{ wordBreak: "break-all" }}>hash {plan.plan_hash.slice(0, 24)}…</div>
              </Panel>
            </div>
          </div>
        </>
      )}

      <div className="stepper-footer">
        <button className="btn ghost" onClick={onBack}><ArrowLeft size={14} /> Back</button>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={onGenerate} disabled={busy !== null}>Regenerate</button>
          <button className="btn primary" onClick={onContinue} disabled={!plan || !plan.validation?.ok}>
            Continue to inspection <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </>
  );
}

function InspectionStage({
  state,
  busy,
  onSign,
  onSignAll,
  onPreflight,
  onBack,
}: {
  state: import("../lib/types").GcsState;
  busy: string | null;
  onSign: (id: string, signed: boolean) => void;
  onSignAll: () => void;
  onPreflight: () => void;
  onBack: () => void;
}) {
  const video = state.video;
  return (
    <>
      <div className="setup-heading">
        <div>
          <div className="micro">Stage 04 · Hardware inspection</div>
          <h1>Inspect aircraft and channels</h1>
          <p>Physical checks are signed by the operator. Software verifies what it can measure; anything unknown remains visibly unknown and blocks readiness.</p>
        </div>
        <Badge tone={video.live_count === 3 ? "ok" : "warn"} dot>{video.live_count}/3 video live</Badge>
      </div>

      <div className="video-wall" style={{ minHeight: 190 }}>
        {video.feeds.map((feed) => (
          <div key={feed.callsign} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <VideoMini feed={feed} mode={state.mission.mode} />
            <div className="small muted mono" style={{ fontSize: 10 }}>{feed.receiver} · {feed.note ?? "fixed mapping"}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 320px", gap: 12 }}>
        <Panel
          title="Manual sign-offs"
          icon={<ShieldCheck size={13} />}
          actions={
            state.mission.mode === "SIMULATION" ? (
              <button className="btn sm" onClick={onSignAll} disabled={busy !== null}>Sign all · simulation</button>
            ) : undefined
          }
        >
          {state.preflight.manual.map((item) => (
            <label className="checkline" key={item.check_id} style={{ cursor: "pointer" }}>
              <span className={clsx("check-icon", item.signed && "ok")}>{item.signed ? <Check size={11} /> : <CircleDashed size={11} />}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12, fontWeight: 700 }}>{item.title}</div>
                <div className="small muted" style={{ marginTop: 2, lineHeight: 1.45 }}>{item.detail}</div>
              </div>
              <input
                type="checkbox"
                checked={item.signed}
                onChange={(event) => onSign(item.check_id, event.target.checked)}
                style={{ marginTop: 3, accentColor: "var(--accent)" }}
              />
            </label>
          ))}
          {state.mission.mode === "SIMULATION" && (
            <div className="small muted" style={{ marginTop: 8 }}>
              Simulation sign-off is labelled in the evidence record and never satisfies a REAL mission gate.
            </div>
          )}
        </Panel>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <Panel title="Fleet identity" icon={<Radio size={13} />}>
            {(Array.isArray(state.fleet.vehicles) ? state.fleet.vehicles : Object.values(state.fleet.vehicles)).map((vehicle) => (
              <div className="kv" key={vehicle.callsign}>
                <span className="k mono">{vehicle.callsign}</span>
                <span className="v" style={{ color: vehicle.identity_valid ? "var(--ok)" : "var(--warn)" }}>
                  {vehicle.identity_valid ? `SYSID ${vehicle.sysid} · ${vehicle.frame}` : "Unverified"}
                </span>
              </div>
            ))}
            <div className="divider" />
            <div className="micro" style={{ lineHeight: 1.6, color: "var(--faint)" }}>A heartbeat SYSID is required. Serial port enumeration is never treated as identity.</div>
          </Panel>
          <Notice tone="warn" icon={<ShieldAlert size={14} />}>
            AI does not participate in inspection. Do not arm with damaged props, loose payloads or an unverified video map.
          </Notice>
        </div>
      </div>

      <div className="stepper-footer">
        <button className="btn ghost" onClick={onBack}><ArrowLeft size={14} /> Back</button>
        <button className="btn primary" onClick={onPreflight} disabled={busy !== null || !state.preflight.manual.every((item) => item.signed)}>
          Run automated preflight <ArrowRight size={14} />
        </button>
      </div>
    </>
  );
}

function VideoMini({ feed, mode }: { feed: import("../lib/types").VideoFeed; mode: string }) {
  const [nonce, setNonce] = useState(Date.now());
  useEffect(() => {
    if (!["LIVE", "RECORDING"].includes(feed.status)) return;
    const timer = window.setInterval(() => setNonce(Date.now()), 480);
    return () => window.clearInterval(timer);
  }, [feed.status]);
  return (
    <div className="video-panel" style={{ aspectRatio: "16 / 9" }}>
      {["LIVE", "RECORDING"].includes(feed.status) ? (
        <img src={`/api/video/${feed.callsign}/frame.jpg?t=${nonce}`} alt={`${feed.callsign} feed`} />
      ) : (
        <div className="no-feed"><span className="mono">{feed.status}</span></div>
      )}
      <div className="scanline" />
      <div className="video-top">
        <Radio size={11} style={{ color: "var(--signal)" }} />
        <span className="mono" style={{ fontWeight: 700 }}>{feed.callsign}</span>
        <span style={{ flex: 1 }} />
        <Badge tone={statusTone(feed.status)} style={{ height: 18, fontSize: 8 }}>{feed.status}</Badge>
      </div>
      {mode === "SIMULATION" && <div className="sim-watermark">SIMULATED</div>}
    </div>
  );
}

function PreflightStage({
  state,
  report,
  gate,
  busy,
  uploading,
  onRun,
  onUpload,
  onGate,
  onBack,
  onContinue,
}: {
  state: import("../lib/types").GcsState;
  report: import("../lib/types").PreflightReport | null;
  gate: Record<string, unknown> | null;
  busy: string | null;
  uploading: boolean;
  onRun: () => void;
  onUpload: () => void;
  onGate: () => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const receipts = (state.mission.upload?.aircraft ?? {}) as Record<string, UploadReceipt>;
  const counts = report?.counts ?? {};
  return (
    <>
      <div className="setup-heading">
        <div>
          <div className="micro">Stage 05 · Automated preflight and upload verification</div>
          <h1>Prove the aircraft are ready</h1>
          <p>Preflight reads live telemetry, video, storage and plan state. Upload verification requires a full mission read-back from every aircraft before the start gate can open.</p>
        </div>
        <Badge tone={report?.ready ? "ok" : "warn"} dot>{report ? `${counts.PASS ?? 0} pass · ${counts.FAIL ?? 0} fail · ${counts.UNKNOWN ?? 0} unknown` : "Not run"}</Badge>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.15fr) minmax(320px,0.85fr)", gap: 12 }}>
        <Panel
          title="Automated checks"
          icon={<ShieldCheck size={13} />}
          actions={<button className="btn sm" onClick={onRun} disabled={busy !== null}>Re-run</button>}
          bodyClass="tight"
        >
          {!report ? (
            <div className="empty">Run preflight to evaluate measured state.</div>
          ) : (
            <div style={{ maxHeight: 420, overflow: "auto" }}>
              {report.checks.map((check) => (
                <div className="checkline" key={check.check_id}>
                  <span className={clsx("check-icon", check.result === "PASS" ? "ok" : check.result === "FAIL" ? "fail" : "warn")}>
                    {check.result === "PASS" ? <Check size={11} /> : check.result === "FAIL" ? <X size={11} /> : <ShieldAlert size={11} />}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", gap: 8, justifyContent: "space-between" }}>
                      <span style={{ fontSize: 11.5, fontWeight: 700 }}>{check.title}</span>
                      <span className="mono" style={{ fontSize: 9.5, color: "var(--muted)" }}>{check.measured ?? ""}</span>
                    </div>
                    <div className="small muted" style={{ marginTop: 1 }}>{check.detail ?? check.threshold}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <Panel title="Mission upload" icon={<UploadCloud size={13} />}>
            <div className="small muted" style={{ marginBottom: 10 }}>
              MAVLink mission items, geofences and parameters are uploaded, then re-read and compared item by item.
            </div>
            <button className="btn primary block" onClick={onUpload} disabled={busy !== null || uploading || !state.mission.plan}>
              {uploading ? <Loader2 size={14} className="spin" /> : <UploadCloud size={14} />}
              {state.mission.upload_verified ? "Re-upload and verify" : "Upload mission"}
            </button>
            <div className="divider" />
            {Object.keys(receipts).length === 0 && <div className="small muted">No receipts yet.</div>}
            {Object.entries(receipts).map(([callsign, receipt]) => (
              <div className="kv" key={callsign}>
                <span className="k mono">{callsign}</span>
                <span className="v">
                  <Badge tone={receipt.status === "VERIFIED" ? "ok" : "danger"}>{receipt.status}</Badge>
                  {receipt.simulated && <span className="micro" style={{ marginLeft: 6 }}>SIM</span>}
                </span>
              </div>
            ))}
          </Panel>

          <Panel title="Readiness gate" icon={<UploadCloud size={13} />}>
            <button className="btn block" onClick={onGate} disabled={busy !== null}>
              <ShieldCheck size={14} /> Evaluate gate
            </button>
            <div className="divider" />
            {!gate ? (
              <div className="small muted">The gate is the only path to START. Unknown is never treated as pass.</div>
            ) : (
              <>
                <Badge tone={gate.ready ? "ok" : "danger"} dot>{gate.ready ? "Gate open" : "Gate blocked"}</Badge>
                <div style={{ marginTop: 8 }}>
                  {((gate.checks as { check_id: string; title: string; ok: boolean; detail: string }[]) ?? []).map((check) => (
                    <div className="kv" key={check.check_id}>
                      <span className="k">{check.title}</span>
                      <span className="v" style={{ color: check.ok ? "var(--ok)" : "var(--danger)" }}>{check.ok ? "PASS" : "BLOCK"}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Panel>
        </div>
      </div>

      <div className="stepper-footer">
        <button className="btn ghost" onClick={onBack}><ArrowLeft size={14} /> Back</button>
        <button className="btn primary" onClick={onContinue}>
          Review launch authority <ArrowRight size={14} />
        </button>
      </div>
    </>
  );
}

function LaunchStage({
  state,
  gate,
  report,
  busy,
  onGate,
  onStart,
  onBack,
  onMission,
}: {
  state: import("../lib/types").GcsState;
  gate: Record<string, unknown> | null;
  report: import("../lib/types").PreflightReport | null;
  busy: string | null;
  onGate: () => void;
  onStart: () => void;
  onBack: () => void;
  onMission: () => void;
}) {
  const ready = Boolean(gate?.ready);
  const executing = state.mission.phase === "EXECUTING";
  return (
    <>
      <div className="setup-heading">
        <div>
          <div className="micro">Stage 06 · Launch authority</div>
          <h1>{executing ? "Mission in progress" : "Final launch decision"}</h1>
          <p>
            The deterministic gate re-evaluates plan, upload evidence, fresh telemetry, pad state, kit inventory and
            manual sign-offs. Absent evidence is a blocker, not a warning.
          </p>
        </div>
        <Badge tone={executing ? "info" : ready ? "ok" : "warn"} dot pulse={executing}>{executing ? "Executing" : ready ? "Cleared" : "Not cleared"}</Badge>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(300px,0.8fr)", gap: 12 }}>
        <Panel title="Gate summary" icon={<ShieldCheck size={13} />}>
          {!gate ? (
            <div className="empty">
              <ShieldAlert size={24} style={{ color: "var(--warn)" }} />
              <div>Gate has not been evaluated with the current state.</div>
              <button className="btn" onClick={onGate} disabled={busy !== null}>Evaluate now</button>
            </div>
          ) : (
            <>
              <div className="stat" style={{ marginBottom: 10 }}>
                <span className="value" style={{ color: ready ? "var(--ok)" : "var(--danger)" }}>{ready ? "CLEARED" : "BLOCKED"}</span>
                <span className="label">Deterministic launch authority</span>
              </div>
              {((gate.checks as { check_id: string; title: string; ok: boolean; detail: string }[]) ?? []).map((check) => (
                <div className="checkline" key={check.check_id} style={{ padding: "8px 0" }}>
                  <span className={clsx("check-icon", check.ok ? "ok" : "fail")}>{check.ok ? <Check size={11} /> : <X size={11} />}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                      <span style={{ fontSize: 11.5, fontWeight: 700 }}>{check.title}</span>
                      <span className="mono" style={{ fontSize: 9.5, color: "var(--muted)" }}>{check.detail}</span>
                    </div>
                  </div>
                </div>
              ))}
            </>
          )}
        </Panel>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <Panel title="Mission record" icon={<FileUp size={13} />}>
            <div className="kv"><span className="k">Mission</span><span className="v mono">{state.mission.mission_id ?? "—"}</span></div>
            <div className="kv"><span className="k">Boundary</span><span className="v">{state.mission.aoi?.area_ha?.toFixed(2) ?? "—"} ha</span></div>
            <div className="kv"><span className="k">Lanes</span><span className="v mono">{state.mission.plan?.search.lane_count ?? "—"}</span></div>
            <div className="kv"><span className="k">Preflight</span><span className="v">{report?.ready ? "Ready" : "Incomplete"}</span></div>
            <div className="kv">
              <span className="k">Evidence</span>
              <span className="v">
                <Badge tone={qualityTone(evidenceQuality(state))}>{evidenceQuality(state)}</Badge>
              </span>
            </div>
          </Panel>
          {executing ? (
            <button className="btn primary block" onClick={onMission}><PlaneTakeoff size={14} /> Open mission command</button>
          ) : (
            <button className="btn primary block" style={{ height: 46 }} onClick={onStart} disabled={!ready || busy !== null}>
              <PlaneTakeoff size={16} /> Authorise launch
            </button>
          )}
          <Notice tone="warn" icon={<ShieldAlert size={14} />}>
            Onboard ArduPilot failsafes remain enabled. Closing the GCS does not disable aircraft protection; use
            EMERGENCY RECALL to bring the fleet home.
          </Notice>
        </div>
      </div>

      <div className="stepper-footer">
        <button className="btn ghost" onClick={onBack}><ArrowLeft size={14} /> Back</button>
        <div className="small muted">START requires the typed confirmation token.</div>
      </div>
    </>
  );
}
