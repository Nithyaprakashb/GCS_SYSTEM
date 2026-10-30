import { useMemo, useState } from "react";
import { Download, Filter, RefreshCw, ScrollText, Search } from "lucide-react";
import { useGcs } from "../state/GcsContext";
import { Badge, Empty, Panel } from "../components/ui";
import { fmtClock, statusTone } from "../lib/format";
import type { GcsEvent } from "../lib/types";

const SEVERITIES = ["all", "critical", "error", "warning", "info", "debug"];

function summarise(event: GcsEvent): string {
  const payload = event.payload ?? {};
  switch (event.topic) {
    case "flight.phase":
      return `${payload.callsign ?? "aircraft"} → ${payload.phase ?? "?"}${payload.detail ? `: ${payload.detail}` : ""}`;
    case "survivor.created":
    case "survivor.updated": {
      const survivor = (payload.survivor ?? {}) as Record<string, unknown>;
      return `${survivor.survivor_id ?? "survivor"} ${survivor.status ?? ""} · confidence ${
        typeof survivor.confidence === "number" ? `${Math.round((survivor.confidence as number) * 100)}%` : "—"
      }`;
    }
    case "allocation.created":
    case "allocation.updated":
    case "allocation.retried": {
      const assignment = (payload.assignment ?? payload) as Record<string, unknown>;
      return `${assignment.survivor_id ?? "survivor"} → ${assignment.callsign ?? assignment.selected ?? "?"} · ${
        assignment.status ?? assignment.rule ?? "decision"
      }`;
    }
    case "delivery.state":
      return `${payload.callsign ?? "?"} / ${payload.survivor_id ?? "?"} · ${payload.previous ?? "?"} → ${payload.state ?? "?"}`;
    case "payload.result":
      return `${payload.kit_id ?? "kit"} · ${payload.result ?? "?"} (beam ${payload.beam_result ?? "—"})`;
    case "safety.interlock":
      return `${payload.rule_id ?? "safety"} — ${payload.detail ?? "interlock"}`;
    case "command.sent": {
      const tx = (payload.tx ?? {}) as Record<string, unknown>;
      return `${tx.callsign ?? "?"} · ${tx.label ?? "command"} · ${tx.result ?? "sent"}`;
    }
    case "mission.phase":
      return `${payload.previous ?? "?"} → ${payload.phase ?? "?"}${payload.reason ? ` · ${payload.reason}` : ""}`;
    case "operator.action":
      return `${payload.action ?? "action"} by ${payload.operator ?? "operator"}${
        payload.mode ? ` · ${payload.mode}` : ""
      }`;
    case "video.health":
      return `${payload.callsign ?? payload.feed_id ?? "feed"} · ${payload.status ?? "?"}${payload.message ? ` · ${payload.message}` : ""}`;
    case "telemetry.link":
      return `${payload.callsign ?? "?"} · ${payload.state ?? payload.detail ?? "link"}`;
    case "fleet.telemetry": {
      const vehicles = Array.isArray(payload.vehicles) ? (payload.vehicles as Record<string, unknown>[]) : [];
      const linked = vehicles.filter((vehicle) => vehicle.connected).length;
      return `${linked}/${vehicles.length} linked · ${payload.mode ?? "?"} · topology ${payload.topology_status ?? payload.topology ?? "?"}`;
    }
    default: {
      const raw = JSON.stringify(payload);
      return raw && raw !== "{}" ? raw.slice(0, 240) : "—";
    }
  }
}

export function LogsScreen() {
  const { state, events: liveEvents, connected, refresh } = useGcs();
  const [severity, setSeverity] = useState("all");
  const [quality, setQuality] = useState("all");
  const [source, setSource] = useState("all");
  const [term, setTerm] = useState("");
  const [limit, setLimit] = useState(300);

  const sourceEvents = liveEvents.length ? liveEvents : (state?.events ?? []);
  // Raw 4 Hz telemetry frames are persisted separately for replay; repeating
  // them here would bury the decision trail. They remain available in the
  // telemetry database and the replay timeline.
  const decisionEvents = useMemo(
    () => sourceEvents.filter((event) => event.topic !== "fleet.telemetry"),
    [sourceEvents],
  );
  const ordered = useMemo(() => {
    const copy = [...decisionEvents];
    copy.sort((a, b) => (a.receive_time < b.receive_time ? 1 : -1));
    return copy;
  }, [decisionEvents]);

  const sources = useMemo(
    () => Array.from(new Set(ordered.map((event) => event.source))).sort(),
    [ordered],
  );
  const qualities = useMemo(
    () => Array.from(new Set(ordered.map((event) => event.data_quality))).sort(),
    [ordered],
  );

  const filtered = useMemo(() => {
    const needle = term.trim().toLowerCase();
    return ordered.filter((event) => {
      if (severity !== "all" && event.severity.toLowerCase() !== severity) return false;
      if (quality !== "all" && event.data_quality !== quality) return false;
      if (source !== "all" && event.source !== source) return false;
      if (!needle) return true;
      return (
        event.topic.toLowerCase().includes(needle) ||
        summarise(event).toLowerCase().includes(needle) ||
        event.source.toLowerCase().includes(needle)
      );
    });
  }, [ordered, severity, quality, source, term]);

  const counts = useMemo(() => {
    let warnings = 0;
    let errors = 0;
    for (const event of ordered) {
      if (event.severity === "warning" || event.severity === "warn") warnings += 1;
      if (event.severity === "error" || event.severity === "critical") errors += 1;
    }
    return { warnings, errors };
  }, [ordered]);

  const exportVisible = () => {
    const lines = filtered.map((event) => JSON.stringify(event)).join("\n");
    const blob = new Blob([lines], { type: "application/x-ndjson" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `rescueswarm-events-${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="page" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <div className="stat">
          <span className="value">{ordered.length}</span>
          <span className="label">Events buffered</span>
        </div>
        <div className="stat">
          <span className="value" style={{ color: "var(--warn)" }}>{counts.warnings}</span>
          <span className="label">Warnings</span>
        </div>
        <div className="stat">
          <span className="value" style={{ color: "var(--danger)" }}>{counts.errors}</span>
          <span className="label">Errors</span>
        </div>
        <div style={{ flex: 1 }} />
        <Badge tone={connected ? "ok" : "danger"} dot pulse={connected}>
          {connected ? "Live stream" : "Stream offline"}
        </Badge>
        <button className="btn sm" onClick={() => void refresh()}>
          <RefreshCw size={12} /> Refresh
        </button>
        <button className="btn sm" onClick={exportVisible} disabled={!filtered.length}>
          <Download size={12} /> Export visible
        </button>
      </div>

      <Panel
        title="Evidence stream"
        icon={<ScrollText size={13} />}
        actions={<span className="micro">{filtered.length} shown · newest first</span>}
        className="fill"
        bodyClass="tight"
      >
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "0 2px 10px" }}>
          <div className="input" style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 220, flex: 1 }}>
            <Search size={13} style={{ color: "var(--muted)", flex: "0 0 auto" }} />
            <input
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder="Search topic, source or summary"
              aria-label="Search events"
              style={{ border: "none", background: "transparent", outline: "none", width: "100%", color: "inherit", fontSize: 12 }}
            />
          </div>
          <select className="select" style={{ width: 140 }} value={severity} onChange={(event) => setSeverity(event.target.value)} aria-label="Filter severity">
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>{value === "all" ? "All severities" : value}</option>
            ))}
          </select>
          <select className="select" style={{ width: 150 }} value={quality} onChange={(event) => setQuality(event.target.value)} aria-label="Filter data quality">
            <option value="all">All data quality</option>
            {qualities.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
          <select className="select" style={{ width: 150 }} value={source} onChange={(event) => setSource(event.target.value)} aria-label="Filter source">
            <option value="all">All sources</option>
            {sources.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
          <select className="select" style={{ width: 116 }} value={limit} onChange={(event) => setLimit(Number(event.target.value))} aria-label="Row limit">
            {[100, 300, 800, 2000].map((value) => (
              <option key={value} value={value}>Last {value}</option>
            ))}
          </select>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, color: "var(--faint)", fontSize: 10 }}>
            <Filter size={11} /> filters apply to the buffer
          </span>
        </div>

        {filtered.length === 0 ? (
          <Empty>
            No events match the current filters. Events are appended by the local backend as telemetry, allocation,
            safety and operator actions occur.
          </Empty>
        ) : (
          <div className="log-scroll">
            <table className="log-table">
              <thead>
                <tr>
                  <th style={{ width: 72 }}>Time</th>
                  <th style={{ width: 190 }}>Topic</th>
                  <th style={{ width: 80 }}>Severity</th>
                  <th style={{ width: 102 }}>Source</th>
                  <th style={{ width: 92 }}>Quality</th>
                  <th>Summary</th>
                </tr>
              </thead>
              <tbody>
                {filtered.slice(0, limit).map((event, index) => (
                  <tr key={event.event_id ?? `${event.receive_time}-${index}`}>
                    <td className="mono" style={{ fontSize: 10 }}>{fmtClock(event.receive_time)}</td>
                    <td className="topic">{event.topic}</td>
                    <td>
                      <Badge tone={statusTone(event.severity.toUpperCase())} className="sm">
                        {event.severity}
                      </Badge>
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>{event.source}</td>
                    <td className="mono" style={{ fontSize: 9.5, color: event.data_quality === "SIMULATED" ? "var(--info)" : "var(--muted)" }}>
                      {event.data_quality}
                    </td>
                    <td style={{ lineHeight: 1.45 }}>{summarise(event)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
