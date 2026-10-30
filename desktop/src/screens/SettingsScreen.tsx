import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Cpu,
  Gauge,
  HardDrive,
  ListChecks,
  MonitorPlay,
  Radio,
  RefreshCw,
  Server,
  Settings2,
  ShieldCheck,
  Wifi,
} from "lucide-react";
import clsx from "clsx";
import { api } from "../lib/api";
import { useGcs } from "../state/GcsContext";
import { Badge, KV, Meter, Notice, Panel } from "../components/ui";
import { humanise } from "../lib/format";

interface FleetRow {
  callsign: string;
  sysid: number;
  role: string;
  frame: string;
  kits: number;
  search_alt_m: number;
  transit_alt_m: number;
}

interface ConfigView {
  profile_name?: string;
  profile_version?: string;
  config_version?: string;
  fleet: FleetRow[];
  mission: Record<string, string | number | null>;
  telemetry: {
    topology?: string;
    topology_verified?: boolean;
    baud?: number;
    radio_region?: string;
  };
}

interface SystemInfo {
  application: string;
  version: string;
  config_version: string;
  profile_version?: string;
  python: string;
  platform: string;
  data_dir: string;
  project_root: string;
  ffmpeg: string | null;
  dependencies: Record<string, boolean>;
  network_policy: string;
  map_policy: string;
}

interface SerialPort {
  port: string;
  description: string;
  hwid: string;
  vid?: number | null;
  pid?: number | null;
  serial_number?: string | null;
  is_modem_candidate?: boolean;
}

interface DeviceView {
  serial: SerialPort[];
  video: { mode: string; devices: { index?: number; name?: string; path?: string; backend?: string }[]; feeds: { callsign: string; receiver: string; fingerprint?: string | null; mapped: boolean }[]; note: string };
  warnings: string[];
}

interface Diagnostic {
  check_id: string;
  title: string;
  status: string;
  detail: string;
  ports?: SerialPort[];
}

function checkTone(status: string): "ok" | "warn" | "danger" {
  if (status === "PASS") return "ok";
  if (status === "FAIL") return "danger";
  return "warn";
}

export function SettingsScreen() {
  const { state, connected } = useGcs();
  const [config, setConfig] = useState<ConfigView | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [devices, setDevices] = useState<DeviceView | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [configResult, infoResult, deviceResult, diagnosticResult] = await Promise.all([
        api.config(),
        api.systemInfo(),
        api.devices(),
        api.diagnostics(),
      ]);
      setConfig(configResult as unknown as ConfigView);
      setInfo(infoResult as unknown as SystemInfo);
      setDevices(deviceResult as unknown as DeviceView);
      setDiagnostics(((diagnosticResult as { checks?: Diagnostic[] }).checks ?? []) as Diagnostic[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to read configuration");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dependencies = info ? Object.entries(info.dependencies) : [];
  const video = state?.video;
  const feeds = video?.feeds ?? [];

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 14, marginBottom: 14, flexWrap: "wrap" }}>
        <div>
          <div className="micro">Local station · read-only view</div>
          <h1 style={{ margin: "3px 0 0", fontSize: 22, letterSpacing: "-.03em" }}>Configuration & hardware</h1>
          <p className="small muted" style={{ margin: "5px 0 0", maxWidth: 760, lineHeight: 1.55 }}>
            Live view of the commissioned profile, enumerated interfaces and safety-relevant policies. Structural
            changes are made in <span className="mono">config/fleet.json</span> and validated at backend startup; they
            are never applied mid-mission.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <Badge tone={connected ? "ok" : "danger"} dot pulse={connected}>
          {connected ? "Local service connected" : "Backend offline"}
        </Badge>
        <button className="btn sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={12} className={loading ? "spin" : undefined} /> Refresh
        </button>
      </div>

      {error && <Notice tone="danger" icon={<Server size={14} />}>{error}</Notice>}

      <div className="settings-grid">
        <Panel title="Operating policy" icon={<ShieldCheck size={13} />}>
          <div className="checkline">
            <div>
              <div className="small ink2"><strong>Offline authority.</strong> {info?.network_policy ?? "Mission operation is local-only; no internet or cloud dependency."}</div>
            </div>
          </div>
          <div className="checkline">
            <div className="small ink2">
              <strong>Onboard protection.</strong> ArduPilot failsafes stay enabled. Closing or crashing the GCS cannot
              disable aircraft protection; ABORT and EMERGENCY RECALL are commands, not replacements for the autopilot.
            </div>
          </div>
          <div className="checkline">
            <div className="small ink2">
              <strong>AI advisory only.</strong> The copilot has no MAVLink command or payload-release authority. Every
              allocation, clearance and release is decided by the deterministic safety and delivery engines.
            </div>
          </div>
          <div className="checkline">
            <div className="small ink2">
              <strong>Evidence.</strong> Operator actions, allocation decisions, uploads and beam evidence are persisted
              locally. Simulated values remain labelled SIMULATED in the UI, database and reports.
            </div>
          </div>
        </Panel>

        <Panel title="Ground station" icon={<Cpu size={13} />}>
          <KV k="Application" v={info?.application ?? "RescueSwarm GCS"} />
          <KV k="Version" v={info?.version ?? "—"} />
          <KV k="Profile" v={`${info?.profile_version ?? config?.profile_version ?? "—"} · config ${info?.config_version ?? config?.config_version ?? "—"}`} />
          <KV k="Python" v={info?.python ?? "—"} />
          <KV k="Platform" v={info?.platform ?? "—"} />
          <KV k="ffmpeg" v={info?.ffmpeg ?? "not found"} />
          <div className="divider" />
          <KV k="Evidence directory" v={<span style={{ wordBreak: "break-all" }}>{info?.data_dir ?? "—"}</span>} />
          <KV k="Project root" v={<span style={{ wordBreak: "break-all" }}>{info?.project_root ?? "—"}</span>} />
        </Panel>

        <Panel title="Fleet profile" icon={<Activity size={13} />} className="span-all">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(230px,1fr))", gap: 10 }}>
            {(config?.fleet ?? []).map((aircraft) => {
              const live = Array.isArray(state?.fleet.vehicles)
                ? state?.fleet.vehicles.find((vehicle) => vehicle.callsign === aircraft.callsign)
                : state?.fleet.vehicles?.[aircraft.callsign];
              return (
                <div key={aircraft.callsign} style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12, background: "var(--surface-2)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <strong className="mono" style={{ fontSize: 13 }}>{aircraft.callsign}</strong>
                    <Badge tone={aircraft.role === "SEARCH" ? "info" : "accent"}>{aircraft.role}</Badge>
                    <div style={{ flex: 1 }} />
                    <Badge tone={live?.connected ? "ok" : "muted"} dot>{live?.connected ? "Linked" : "Offline"}</Badge>
                  </div>
                  <div className="small muted" style={{ margin: "7px 0 4px" }}>{aircraft.frame}</div>
                  <KV k="System ID" v={aircraft.sysid} />
                  <KV k="Search / transit" v={`${aircraft.search_alt_m ?? "—"} m / ${aircraft.transit_alt_m ?? "—"} m`} />
                  <KV k="Payload" v={aircraft.kits ? `${aircraft.kits} kits` : "camera only"} />
                  {typeof live?.battery_pct === "number" && (
                    <div style={{ marginTop: 8 }}>
                      <Meter value={live.battery_pct} tone={live.battery_pct > 40 ? "ok" : live.battery_pct > 22 ? "warn" : "danger"} />
                      <div className="micro" style={{ marginTop: 4 }}>Pack {live.battery_pct.toFixed(0)}%</div>
                    </div>
                  )}
                </div>
              );
            })}
            {!config?.fleet?.length && <div className="empty">Fleet profile unavailable.</div>}
          </div>
        </Panel>

        <Panel title="Telemetry topology" icon={<Radio size={13} />}>
          <KV k="Configured topology" v={config?.telemetry.topology ?? "—"} />
          <KV k="Commissioned" v={config?.telemetry.topology_verified ? "Yes" : "No"} />
          <KV k="Baud" v={config?.telemetry.baud ?? "—"} />
          <KV k="Region" v={config?.telemetry.radio_region ?? "—"} />
          <Notice tone="warn" icon={<Radio size={14} />}>
            A detected USB serial interface is not a paired RFD868x link. The profile only marks this topology as
            commissioned after a verified shared multipoint link or three independent point-to-point links are proven
            with all three aircraft.
          </Notice>
        </Panel>

        <Panel title="Video channels" icon={<MonitorPlay size={13} />}>
          <KV k="Pipeline mode" v={video?.mode ?? "—"} />
          <KV k="Feeds live" v={`${video?.live_count ?? 0} / ${video?.total ?? feeds.length}`} />
          <KV k="Recording" v={`${video?.recording_count ?? 0} channel(s)`} />
          <div className="divider" />
          {feeds.map((feed) => (
            <div className="device-row" key={feed.feed_id}>
              <div>
                <div className="name">{feed.callsign} · {feed.receiver}</div>
                <div className="meta">{feed.note ?? "Fixed receiver-to-aircraft mapping"}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <Badge tone={feed.mapped ? "ok" : "warn"}>{feed.mapped ? "Mapped" : "Unmapped"}</Badge>
                <div className="meta">{feed.status}{feed.recording ? " · REC" : ""}</div>
              </div>
            </div>
          ))}
          {!feeds.length && <div className="empty">Video service has not initialised.</div>}
          <div className="divider" />
          <div className="micro" style={{ lineHeight: 1.6, color: "var(--faint)" }}>
            RunCam analog feeds are recorded independently. No YOLO overlay is drawn on these channels; the Pi AI camera
            is a separate sensor and is not assumed to be synchronised.
          </div>
        </Panel>

        <Panel title="Mission limits" icon={<Gauge size={13} />}>
          <KV k="Merge radius" v={`${config?.mission.merge_distance_m ?? "—"} m`} />
          <KV k="Release clearance" v={`${config?.mission.descent_clearance_m ?? "—"} m`} />
          <KV k="Time cap" v={`${config?.mission.time_cap_min ?? "—"} min`} />
          <KV k="Max survivors" v={config?.mission.max_survivors ?? "—"} />
          <KV k="Pad" v={`${config?.mission.pad_name ?? "—"} · ${config?.mission.pad_lat ?? "—"}, ${config?.mission.pad_lon ?? "—"}`} />
          <KV k="Map source" v={info?.map_policy ?? "Local procedural tiles"} />
        </Panel>

        <Panel title="Serial interfaces" icon={<Wifi size={13} />}>
          {(devices?.serial ?? []).map((port) => (
            <div className="device-row" key={port.port}>
              <div>
                <div className="name">{port.port}</div>
                <div className="meta">{port.description || "No description"} · {port.hwid || "no hardware id"}</div>
              </div>
              <Badge tone={port.is_modem_candidate ? "info" : "muted"}>{port.is_modem_candidate ? "Candidate" : "Other"}</Badge>
            </div>
          ))}
          {!devices?.serial?.length && <div className="empty">No serial interfaces enumerated.</div>}
          <div className="divider" />
          <div className="micro" style={{ lineHeight: 1.55, color: "var(--faint)" }}>
            Enumeration only. Pairing requires a verified MAVLink session with the correct system ID and component
            identity; the GCS will not infer a link from port order.
          </div>
        </Panel>

        <Panel title="Startup diagnostics" icon={<ListChecks size={13} />}>
          {diagnostics.map((check) => (
            <div className="checkline" key={check.check_id} style={{ padding: "8px 0" }}>
              <div style={{ flex: 1 }}>
                <div className="small" style={{ fontWeight: 600 }}>{check.title}</div>
                <div className="micro" style={{ marginTop: 2, color: "var(--faint)" }}>{check.detail}</div>
              </div>
              <Badge tone={checkTone(check.status)}>{check.status}</Badge>
            </div>
          ))}
          {!diagnostics.length && <div className="empty">Diagnostics unavailable.</div>}
        </Panel>

        <Panel title="Runtime dependencies" icon={<HardDrive size={13} />}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {dependencies.map(([name, present]) => (
              <Badge key={name} tone={present ? "ok" : "danger"} dot>
                {name}
              </Badge>
            ))}
          </div>
          <div className="divider" />
          <div className="micro" style={{ lineHeight: 1.6, color: "var(--faint)" }}>
            No runtime dependency contacts an external service. Missing packages degrade only their dependent feature
            and are reported in diagnostics; they are never silently substituted.
          </div>
        </Panel>

        <Panel title="Capture devices" icon={<MonitorPlay size={13} />}>
          {(devices?.video.devices ?? []).map((device, index) => (
            <div className="device-row" key={`${device.path ?? device.name ?? index}`}>
              <div>
                <div className="name">{device.name ?? device.path ?? `device ${index}`}</div>
                <div className="meta">{device.path ?? device.backend ?? "capture device"}</div>
              </div>
              <Badge tone="muted">Enumerated</Badge>
            </div>
          ))}
          {!devices?.video.devices?.length && (
            <div className="empty">
              {devices?.video.mode === "SIMULATION"
                ? "Simulation mode: three synthetic feeds, no capture hardware claimed."
                : "No capture devices enumerated."}
            </div>
          )}
          <div className="divider" />
          <div className="micro" style={{ lineHeight: 1.55, color: "var(--faint)" }}>
            {devices?.video.note ?? "Enumeration order does not establish aircraft identity."}
          </div>
        </Panel>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14, color: "var(--faint)", fontSize: 10 }}>
        <Settings2 size={12} />
        <span className={clsx("mono")}>{state?.app.data_dir}</span>
        <span>·</span>
        <span>Policy is enforced by the backend; this screen cannot bypass it.</span>
        <span>·</span>
        <span>{humanise(state?.mission.mode)} mode</span>
      </div>
    </div>
  );
}
