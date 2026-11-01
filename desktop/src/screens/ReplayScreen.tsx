import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  CalendarClock,
  Download,
  FileText,
  MapPin,
  Package,
  Pause,
  Play,
  RefreshCw,
  Rewind,
  Route,
} from "lucide-react";
import clsx from "clsx";
import { api, type ReplayPayload, type ReplaySample } from "../lib/api";
import { fmtClock, fmtDateTime, fmtNumber, fmtTime, humanise, statusTone } from "../lib/format";
import type { MissionListEntry, MissionPlan, Survivor, Vehicle } from "../lib/types";
import { Badge, Empty, KV, Modal, Panel } from "../components/ui";
import { TacticalMap } from "../components/TacticalMap";
import { useGcs } from "../state/GcsContext";

interface Analytics {
  duration_s?: number | null;
  duration_min?: number | null;
  survivors_registered?: number;
  survivor_status?: Record<string, number>;
  kits_delivered?: number;
  kits_unknown?: number;
  kits_remaining?: number;
  event_topics?: Record<string, number>;
  telemetry?: Record<string, number>;
  allocations?: number;
  evidence?: string;
}

const SPEEDS = [10, 30, 60, 120];

function emptyVehicle(callsign: string): Vehicle {
  return {
    callsign,
    sysid: 0,
    role: callsign.startsWith("INS") ? "SEARCH" : "DELIVERY",
    frame: callsign.startsWith("INS") ? "650 mm X4" : "900 mm X6",
    connected: true,
    link: "RECORDED",
    identity_valid: true,
    mode: "—",
    armed: false,
    lat: null,
    lon: null,
    alt_rel_m: null,
    agl_m: null,
    groundspeed_ms: null,
    heading_deg: null,
    battery_pct: null,
    battery_voltage: null,
    gps_fix: 3,
    gps_sats: 0,
    ekf_ok: true,
    rangefinder_m: null,
    link_quality: null,
    rssi: null,
    heartbeat_age_s: null,
    stale: true,
    data_quality: "RECORDED",
    task: "RECORDED",
    failsafe: null,
    queue_depth: 0,
    kits_free: 0,
    search_alt_m: 12,
    transit_alt_m: 0,
    statustext: "",
  };
}

function timeOf(sample: ReplaySample): number {
  return new Date(sample.t).getTime();
}

export function ReplayScreen({ initialMissionId }: { initialMissionId?: string | null }) {
  const { run } = useGcs();
  const [missions, setMissions] = useState<MissionListEntry[]>([]);
  const [missionId, setMissionId] = useState<string | null>(initialMissionId ?? null);
  const [replay, setReplay] = useState<ReplayPayload | null>(null);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(30);
  const [playhead, setPlayhead] = useState(0);
  const [reportOpen, setReportOpen] = useState(false);
  const timer = useRef<number | null>(null);

  const loadMissions = async () => {
    try {
      const result = await api.missions();
      setMissions(result.missions);
      if (!missionId && result.missions.length) setMissionId(result.missions[0].mission_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Mission archive unavailable");
    }
  };

  useEffect(() => {
    void loadMissions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;
    setLoading(true);
    setReplay(null);
    setAnalytics(null);
    setPlaying(false);
    Promise.all([api.replay(missionId), api.analytics(missionId)])
      .then(([replayData, analyticsData]) => {
        if (cancelled) return;
        setReplay(replayData);
        setAnalytics(analyticsData as Analytics);
        const times = Object.values(replayData.tracks).flat().map(timeOf).filter((t) => Number.isFinite(t));
        setPlayhead(times.length ? Math.min(...times) : 0);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Replay unavailable");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [missionId]);

  const range = useMemo(() => {
    if (!replay) return { start: 0, end: 1 };
    const times = Object.values(replay.tracks).flat().map(timeOf).filter((t) => Number.isFinite(t));
    if (!times.length) return { start: 0, end: 1 };
    return { start: Math.min(...times), end: Math.max(...times) };
  }, [replay]);

  useEffect(() => {
    if (!playing) {
      if (timer.current) window.clearInterval(timer.current);
      return;
    }
    timer.current = window.setInterval(() => {
      setPlayhead((current) => {
        const next = current + 120 * speed;
        if (next >= range.end) {
          setPlaying(false);
          return range.end;
        }
        return next;
      });
    }, 120);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [playing, speed, range.end]);

  const positions = useMemo(() => {
    const result: Record<string, { lat: number; lon: number; heading_deg: number; connected: boolean; role: string }> = {};
    if (!replay) return result;
    for (const [callsign, samples] of Object.entries(replay.tracks)) {
      let last: ReplaySample | null = null;
      for (const sample of samples) {
        if (timeOf(sample) <= playhead) last = sample;
        else break;
      }
      if (last) {
        result[callsign] = {
          lat: last.lat,
          lon: last.lon,
          heading_deg: last.heading_deg ?? 0,
          connected: true,
          role: callsign.startsWith("INS") ? "SEARCH" : "DELIVERY",
        };
      }
    }
    return result;
  }, [replay, playhead]);

  const trails = useMemo(() => {
    const result: Record<string, { lon: number; lat: number }[]> = {};
    if (!replay) return result;
    for (const [callsign, samples] of Object.entries(replay.tracks)) {
      result[callsign] = samples.filter((sample) => timeOf(sample) <= playhead).map((sample) => ({ lon: sample.lon, lat: sample.lat }));
    }
    return result;
  }, [replay, playhead]);

  const baseVehicles = useMemo(() => {
    if (!replay) return [] as Vehicle[];
    return Object.keys(replay.tracks).map(emptyVehicle);
  }, [replay]);

  const replaySurvivors = useMemo(() => {
    if (!replay) return [] as Survivor[];
    return replay.markers
      .filter((marker) => !marker.first_seen || new Date(marker.first_seen).getTime() <= playhead)
      .map(
        (marker) =>
          ({
            ...marker,
            status: marker.status as Survivor["status"],
            confidence: 1,
            observations: 1,
            data_quality: marker.data_quality ?? "RECORDED",
          }) as Survivor,
      );
  }, [replay, playhead]);

  const timeline = useMemo(() => {
    if (!replay) return [];
    return replay.timeline
      .filter((event) => new Date(event.t).getTime() <= playhead)
      .slice(-10)
      .reverse();
  }, [replay, playhead]);

  const insSamples = replay?.tracks["INS-01"] ?? [];
  const chart = useMemo(() => {
    if (insSamples.length < 2) return "";
    const minT = timeOf(insSamples[0]);
    const maxT = timeOf(insSamples[insSamples.length - 1]);
    const maxAlt = Math.max(...insSamples.map((sample) => sample.alt_rel_m ?? 0), 1);
    return insSamples
      .map((sample) => {
        const x = ((timeOf(sample) - minT) / Math.max(1, maxT - minT)) * 320;
        const y = 96 - ((sample.alt_rel_m ?? 0) / maxAlt) * 82;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }, [insSamples]);

  const selectedMission = missions.find((mission) => mission.mission_id === missionId);
  const plan = replay?.plan as MissionPlan | null | undefined;
  const progress = range.end > range.start ? ((playhead - range.start) / (range.end - range.start)) * 100 : 0;

  const regenerate = async () => {
    if (!missionId) return;
    const result = await run("Regenerate report", () => api.regenerateReport(missionId), "Report regenerated");
    if (result) setReportOpen(true);
  };

  return (
    <div className="page" style={{ overflow: "hidden" }}>
      <div className="replay-layout">
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
          <Panel
            title="Mission archive"
            icon={<CalendarClock size={13} />}
            actions={<button className="icon-button" onClick={() => void loadMissions()} aria-label="Refresh archive"><RefreshCw size={13} /></button>}
            bodyClass="tight"
          >
            {missions.length === 0 ? (
              <Empty>No recorded missions.</Empty>
            ) : (
              <div className="mission-list">
                {missions.map((mission) => (
                  <button
                    key={mission.mission_id}
                    className={clsx("mission-item", missionId === mission.mission_id && "active")}
                    onClick={() => setMissionId(mission.mission_id)}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                      <span className="id">{mission.mission_id}</span>
                      <Badge tone={statusTone(mission.outcome ?? mission.phase)} style={{ height: 16, fontSize: 7.5, padding: "0 4px" }}>
                        {humanise(mission.outcome ?? mission.phase)}
                      </Badge>
                    </div>
                    <div className="nm">{mission.name}</div>
                    <div className="dt">{fmtDateTime(mission.started_at ?? mission.created_at)} · {mission.mode}</div>
                  </button>
                ))}
              </div>
            )}
          </Panel>

          {selectedMission && (
            <Panel title="Record" icon={<FileText size={13} />}>
              <KV k="Mission" v={selectedMission.mission_id} />
              <KV k="Mode" v={selectedMission.mode} />
              <KV k="Started" v={fmtDateTime(selectedMission.started_at)} />
              <KV k="Ended" v={fmtDateTime(selectedMission.ended_at)} />
              <KV k="Outcome" v={humanise(selectedMission.outcome)} />
              <div className="divider" />
              <div style={{ display: "flex", gap: 6 }}>
                <a className="btn sm" href={`/api/missions/${selectedMission.mission_id}/report.pdf`} download>
                  <Download size={12} /> PDF
                </a>
                <button className="btn sm" onClick={() => void regenerate()} disabled={!missionId}>
                  <RefreshCw size={12} /> Rebuild
                </button>
              </div>
            </Panel>
          )}

          <Panel title="Evidence" icon={<Activity size={13} />}>
            <div className="small ink2" style={{ lineHeight: 1.55 }}>
              {replay?.evidence ?? "Select a mission to load its recorded telemetry, events and survivor track."}
            </div>
            <div className="divider" />
            <div className="micro" style={{ lineHeight: 1.6, color: "var(--faint)" }}>
              Replay is reconstructed from persisted samples; simulated values remain labelled SIMULATED.
            </div>
          </Panel>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
          <div style={{ flex: 1, minHeight: 300, display: "flex", flexDirection: "column" }}>
            <TacticalMap
              aoi={plan?.aoi ?? null}
              plan={plan ?? null}
              vehicles={baseVehicles}
              aircraftOverride={positions}
              trails={trails}
              survivors={replaySurvivors}
              provenance="RECORDED"
              className="fill"
            />
          </div>
          <div className="panel" style={{ flex: 0 }}>
            <div className="scrubber" style={{ borderTop: "none" }}>
              <button className="btn icon sm" onClick={() => { setPlayhead(range.start); setPlaying(false); }} aria-label="Rewind">
                <Rewind size={13} />
              </button>
              <button className="btn icon sm primary" onClick={() => setPlaying((value) => !value)} disabled={!replay} aria-label={playing ? "Pause" : "Play"}>
                {playing ? <Pause size={13} /> : <Play size={13} />}
              </button>
              <span className="mono small" style={{ minWidth: 52 }}>{fmtClock(new Date(playhead).toISOString())}</span>
              <input
                type="range"
                min={range.start}
                max={range.end}
                step={500}
                value={playhead}
                onChange={(event) => { setPlayhead(Number(event.target.value)); setPlaying(false); }}
                aria-label="Replay timeline"
              />
              <span className="mono small muted">{Math.round(progress)}%</span>
              <div className="tabs">
                {SPEEDS.map((option) => (
                  <button key={option} className={speed === option ? "active" : ""} onClick={() => setSpeed(option)}>{option}×</button>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="mission-col" style={{ overflow: "auto" }}>
          <Panel title="Analytics" icon={<Activity size={13} />} bodyClass="tight">
            {loading && <div className="empty">Loading record…</div>}
            {error && <div className="notice danger">{error}</div>}
            {analytics && (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
                  <div><div className="micro">Duration</div><div className="mono" style={{ fontSize: 20 }}>{fmtTime(analytics.duration_s)}</div></div>
                  <div><div className="micro">Survivors</div><div className="mono" style={{ fontSize: 20 }}>{analytics.survivors_registered ?? 0}/10</div></div>
                  <div><div className="micro">Delivered</div><div className="mono" style={{ fontSize: 20, color: "var(--ok)" }}>{analytics.survivor_status?.DELIVERED ?? 0}</div></div>
                  <div><div className="micro">Unknown</div><div className="mono" style={{ fontSize: 20, color: (analytics.survivor_status?.UNKNOWN ?? 0) > 0 ? "var(--warn)" : "var(--muted)" }}>{analytics.survivor_status?.UNKNOWN ?? 0}</div></div>
                </div>
                <div className="micro" style={{ marginBottom: 5 }}>Survivor outcomes</div>
                {Object.entries(analytics.survivor_status ?? {}).map(([status, count]) => (
                  <div className="bar-row" key={status}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{humanise(status)}</span>
                    <div className="bar"><span style={{ width: `${(count / Math.max(1, analytics.survivors_registered ?? 1)) * 100}%`, background: status === "DELIVERED" ? "var(--ok)" : status === "UNKNOWN" ? "var(--warn)" : "var(--accent)" }} /></div>
                    <span className="mono" style={{ textAlign: "right" }}>{count}</span>
                  </div>
                ))}
                <div className="divider" />
                <div className="kv"><span className="k">Kits delivered</span><span className="v mono">{analytics.kits_delivered ?? 0}</span></div>
                <div className="kv"><span className="k">Kits unknown</span><span className="v mono">{analytics.kits_unknown ?? 0}</span></div>
                <div className="kv"><span className="k">Kits remaining</span><span className="v mono">{analytics.kits_remaining ?? 0}</span></div>
                <div className="kv"><span className="k">Allocations</span><span className="v mono">{analytics.allocations ?? 0}</span></div>
                <div className="micro" style={{ marginTop: 6, color: "var(--faint)" }}>{analytics.evidence}</div>
              </>
            )}
          </Panel>

          <Panel title="Altitude · INS-01" icon={<Route size={13} />} bodyClass="tight">
            {chart ? (
              <svg className="track-chart" viewBox="0 0 320 100" preserveAspectRatio="none" aria-label="Search aircraft altitude profile">
                <defs>
                  <linearGradient id="altfill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#5ad1c8" stopOpacity="0.35" />
                    <stop offset="100%" stopColor="#5ad1c8" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <polyline points={`0,96 ${chart} 320,96`} fill="url(#altfill)" stroke="none" />
                <polyline points={chart} fill="none" stroke="#5ad1c8" strokeWidth="1.3" />
              </svg>
            ) : (
              <Empty>No recorded altitude samples.</Empty>
            )}
            <div style={{ display: "flex", justifyContent: "space-between" }} className="micro">
              <span>Launch</span><span>Search</span><span>RTL</span>
            </div>
          </Panel>

          <Panel title="Event timeline" icon={<MapPin size={13} />} bodyClass="tight">
            <div className="timeline" style={{ maxHeight: 260 }}>
              {timeline.length === 0 ? (
                <Empty>No events before this point.</Empty>
              ) : (
                timeline.map((event, index) => (
                  <div className="timeline-item" key={`${event.t}-${index}`}>
                    <span className="timeline-time">{fmtClock(event.t)}</span>
                    <span className={clsx("timeline-dot", statusTone(event.severity))} />
                    <span className="timeline-text">{event.summary}</span>
                  </div>
                ))
              )}
            </div>
          </Panel>

          <Panel title="Telemetry coverage" icon={<Package size={13} />}>
            {Object.entries(analytics?.telemetry ?? {}).map(([callsign, count]) => (
              <div className="kv" key={callsign}>
                <span className="k mono">{callsign}</span>
                <span className="v mono">{fmtNumber(count as number)} samples</span>
              </div>
            ))}
            <div className="divider" />
            <div className="small muted">Recorded at the evidence cadence; gaps remain gaps.</div>
          </Panel>
        </div>
      </div>

      <Modal
        open={reportOpen}
        onClose={() => setReportOpen(false)}
        title="Report rebuild"
        footer={
          missionId ? (
            <a className="btn primary" href={`/api/missions/${missionId}/report.pdf`} download>
              Download PDF
            </a>
          ) : (
            /* A dead <a href="#"> styled as a primary button is a placeholder control: it looks
              armed, fires nothing and teaches the operator that this screen lies. */
            <span className="small muted">Select a mission to download its report.</span>
          )
        }
      >
        <p className="modal-text">
          The report has been regenerated from recorded events, telemetry and decisions. Calculated values remain
          labelled CALCULATED and simulated evidence remains labelled SIMULATED.
        </p>
      </Modal>
    </div>
  );
}
