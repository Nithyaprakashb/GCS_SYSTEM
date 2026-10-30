import { useEffect, useState } from "react";
import { Circle, Maximize2, Radio, VideoOff } from "lucide-react";
import type { VideoFeed } from "../lib/types";

export function VideoPanel({
  feed,
  mode,
  onExpand,
}: {
  feed: VideoFeed | undefined;
  mode: string;
  onExpand?: () => void;
}) {
  const [nonce, setNonce] = useState(() => Date.now());
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
    if (!feed || !["LIVE", "RECORDING"].includes(feed.status)) return;
    const timer = window.setInterval(() => setNonce(Date.now()), 380);
    return () => window.clearInterval(timer);
  }, [feed?.status, feed?.callsign]);

  const live = feed ? ["LIVE", "RECORDING"].includes(feed.status) : false;
  const callsign = feed?.callsign ?? "—";

  return (
    <div className="video-panel">
      {live && !failed ? (
        <img
          src={`/api/video/${callsign}/frame.jpg?t=${nonce}`}
          alt={`${callsign} analog video feed`}
          onError={() => setFailed(true)}
          draggable={false}
        />
      ) : (
        <div className="no-feed">
          <VideoOff size={22} style={{ marginBottom: 6, opacity: 0.6 }} />
          <div style={{ fontSize: 11 }}>{feed?.status === "BLACK" ? "BLACK / NO SIGNAL" : feed?.status === "FROZEN" ? "FEED FROZEN" : "NO ANALOG SIGNAL"}</div>
          <div className="micro" style={{ marginTop: 5, color: "var(--faint)" }}>{callsign}</div>
        </div>
      )}
      <div className="scanline" />
      <div className="video-top">
        <Radio size={11} style={{ color: "var(--signal)" }} />
        <span className="mono" style={{ fontWeight: 700 }}>{callsign}</span>
        <span style={{ color: "var(--faint)" }}>TX800 · 5.8 GHz</span>
        <span style={{ flex: 1 }} />
        {feed?.recording && (
          <span className="badge danger" style={{ height: 18, fontSize: 8.5, padding: "0 6px" }}>
            <Circle size={5} fill="currentColor" /> REC
          </span>
        )}
        {onExpand && (
          <button className="icon-button" style={{ width: 20, height: 20, color: "#c6d2e2" }} onClick={onExpand} aria-label="Expand feed">
            <Maximize2 size={11} />
          </button>
        )}
      </div>
      {mode === "SIMULATION" && <div className="sim-watermark">SIMULATED</div>}
      <div className="video-bottom">
        <span>{feed?.status ?? "OFFLINE"}</span>
        <span>NO AI OVERLAY · UNSYNCHRONISED</span>
        <span>{feed?.fps ? `${feed.fps.toFixed(1)} fps` : "—"}</span>
        <span title="Capture-to-display estimate, measured locally">{feed?.latency_estimate_ms != null ? `${Math.round(feed.latency_estimate_ms)} ms` : "— ms"}</span>
      </div>
      {feed?.message && !["LIVE", "RECORDING"].includes(feed.status) ? (
        <div className="video-alert mono">{feed.message}</div>
      ) : null}
    </div>
  );
}
