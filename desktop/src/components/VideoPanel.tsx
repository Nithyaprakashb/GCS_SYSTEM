import { useEffect, useState } from "react";
import { Circle, Maximize2, Radio, VideoOff } from "lucide-react";
import clsx from "clsx";
import type { VideoFeed } from "../lib/types";

/**
 * One analog video channel.
 *
 * Every number here is measured at this station or it is not shown. The GCS cannot see
 * the far end of a 5.8 GHz link, so glass-to-glass latency is reported as unmeasured
 * with the reason, rather than as the plausible-looking figure it never was.
 */

const LIVE_STATES = ["LIVE", "RECORDING"];

const LATENCY_BASIS: Record<string, string> = {
  NOT_MEASURED:
    "Latency has never been measured on this station. TX800 glass-to-glass delay needs a timing pattern filmed on the ground, which has not been done.",
  SYNTHETIC_FEED_NO_LINK:
    "Synthetic frame from the simulator — there is no radio in this path, so link latency does not apply.",
  MEASURED: "Measured at this station from transmitted timestamp to displayed frame.",
};

function ageLabel(feed: VideoFeed | undefined): string {
  if (!feed) return "no feed";
  // A pipeline that has never produced a frame has no age to count — saying "AGE —"
  // next to it reads like a missing measurement rather than a dead feed.
  if (feed.no_frame_s != null) return "AGE no frame yet";
  if (feed.frame_age_ms === null || feed.frame_age_ms === undefined) return "AGE —";
  const seconds = feed.frame_age_ms / 1000;
  return `AGE ${seconds < 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds)} s`}`;
}

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
  const live = Boolean(feed && LIVE_STATES.includes(feed.status));

  useEffect(() => {
    setFailed(false);
    if (!live) return;
    const timer = window.setInterval(() => setNonce(Date.now()), 380);
    return () => window.clearInterval(timer);
  }, [live, feed?.callsign]);

  const callsign = feed?.callsign ?? "—";
  const status = feed?.status ?? "OFFLINE";
  const frozen = status === "FROZEN";
  const degraded = !live && status !== "OFFLINE" && status !== "UNMAPPED";
  const basis = feed?.latency_basis ?? "NOT_MEASURED";
  const latencyText =
    feed?.latency_estimate_ms != null ? `${Math.round(feed.latency_estimate_ms)} ms` : "LAT —";

  return (
    <div className="video-panel">
      {live && !failed ? (
        <img
          src={`/api/video/${callsign}/frame.jpg?t=${nonce}`}
          alt={`${callsign} analog video feed — ${status.toLowerCase()}, ${ageLabel(feed)}`}
          onError={() => setFailed(true)}
          draggable={false}
        />
      ) : (
        <div className="no-feed">
          <VideoOff size={22} style={{ marginBottom: 6, opacity: 0.6 }} />
          <div style={{ fontSize: 11 }}>{humanStatus(status)}</div>
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
          <button className="icon-button" style={{ width: 20, height: 20, color: "#c6d2e2" }} onClick={onExpand} aria-label={`Expand ${callsign} video feed`}>
            <Maximize2 size={11} />
          </button>
        )}
      </div>
      {mode === "SIMULATION" && <div className="sim-watermark">SIMULATED</div>}
      <div className="video-bottom">
        <span className={clsx((frozen || degraded) && "alert")}>{status}</span>
        <span>NO AI OVERLAY · UNSYNCHRONISED</span>
        <span title="Frames per second arriving at this station, counted locally">
          {feed?.fps ? `${feed.fps.toFixed(1)} fps` : "— fps"}
        </span>
        <span title="Time since the last frame was received here">{ageLabel(feed)}</span>
        <span className="lat" title={LATENCY_BASIS[basis] ?? `Latency basis: ${basis}`}>
          {latencyText}
        </span>
      </div>
      {feed && (frozen || degraded || status === "UNMAPPED") && feed.message ? (
        <div className="video-alert mono">{feed.message}</div>
      ) : null}
      {feed && (frozen || degraded) ? (
        <div className="video-cues mono">
          <span>{`${feed.frames_received ?? 0} frames in`}</span>
          {feed.no_frame_s != null ? (
            <span className="alert">{`silent ${Math.round(feed.no_frame_s)} s since capture start`}</span>
          ) : null}
          {feed.dropped ? <span className="alert">{`${feed.dropped} undecodable`}</span> : null}
          {frozen && feed.frozen_s ? <span className="alert">{`no new frame ${feed.frozen_s.toFixed(1)} s`}</span> : null}
          {feed.source === "SIMULATED" ? <span>synthetic · not radio</span> : null}
        </div>
      ) : null}
    </div>
  );
}

function humanStatus(status: string): string {
  if (status === "BLACK") return "BLACK / NO LIGHT";
  if (status === "FROZEN") return "FEED FROZEN — LAST FRAME HELD";
  if (status === "UNMAPPED") return "NOT COMMISSIONED";
  if (status === "CAPTURE_ERROR") return "CAPTURE ERROR";
  if (status === "NO_SIGNAL") return "NO ANALOG SIGNAL";
  return status;
}
