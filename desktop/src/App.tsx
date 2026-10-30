import { useEffect, useMemo, useRef, useState } from "react";
import {
  Crosshair,
  History,
  Loader2,
  Moon,
  RotateCcw,
  ScrollText,
  Settings2,
  ShieldAlert,
  SlidersHorizontal,
  Sun,
  TriangleAlert,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import clsx from "clsx";
import { api } from "./lib/api";
import { humanise } from "./lib/format";
import { Badge, Logo, Modal, Notice } from "./components/ui";
import { useGcs } from "./state/GcsContext";
import { BootScreen } from "./screens/BootScreen";
import { SetupScreen } from "./screens/SetupScreen";
import { MissionScreen } from "./screens/MissionScreen";
import { ReplayScreen } from "./screens/ReplayScreen";
import { LogsScreen } from "./screens/LogsScreen";
import { SettingsScreen } from "./screens/SettingsScreen";

type Screen = "setup" | "mission" | "replay" | "logs" | "settings";
type ConfirmKind = "abort" | "recall";

const NAV: { id: Screen; label: string; icon: typeof Crosshair }[] = [
  { id: "mission", label: "Mission command", icon: Crosshair },
  { id: "setup", label: "Mission setup", icon: SlidersHorizontal },
  { id: "replay", label: "Mission archive", icon: History },
  { id: "logs", label: "Event log", icon: ScrollText },
  { id: "settings", label: "Configuration", icon: Settings2 },
];

const TITLES: Record<Screen, { title: string; sub: string }> = {
  mission: { title: "Mission command", sub: "Tactical operations picture" },
  setup: { title: "Mission setup", sub: "Six-stage launch workflow" },
  replay: { title: "Mission archive", sub: "Recorded flight reconstruction" },
  logs: { title: "Event log", sub: "Local evidence stream" },
  settings: { title: "Configuration", sub: "Hardware, links and operating policy" },
};

export function App() {
  const { state, connected, loading, error, theme, setTheme, run, busy, refresh, toasts, dismissToast } = useGcs();
  const [booted, setBooted] = useState(false);
  const [screen, setScreen] = useState<Screen>("setup");
  const [replayMission, setReplayMission] = useState<string | null>(null);
  const [confirmKind, setConfirmKind] = useState<ConfirmKind | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [reason, setReason] = useState("Operator command from GCS");
  const [clock, setClock] = useState(() => new Date());
  const previousPhase = useRef<string | null>(null);

  const mission = state?.mission;
  const phase = mission?.phase ?? "STANDBY";
  const executing = phase === "EXECUTING";

  const vehicles = useMemo(() => {
    const source = state?.fleet.vehicles;
    if (!source) return [];
    return Array.isArray(source) ? source : Object.values(source);
  }, [state?.fleet.vehicles]);
  const online = vehicles.filter((vehicle) => vehicle.connected && !vehicle.stale).length;

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const current = mission?.phase ?? null;
    if (current === "EXECUTING" && previousPhase.current !== "EXECUTING") {
      setScreen("mission");
    }
    previousPhase.current = current;
  }, [mission?.phase]);

  useEffect(() => {
    if (!confirmKind) return;
    setConfirmText("");
    setReason(confirmKind === "recall" ? "Emergency recall — operator command" : "Operator abort — hold aircraft");
  }, [confirmKind]);

  const enter = () => {
    setBooted(true);
    const current = mission?.phase;
    setScreen(current === "EXECUTING" || current === "COMPLETE" || current === "ABORTED" ? "mission" : "setup");
  };

  const openReplay = (missionId: string) => {
    setReplayMission(missionId);
    setScreen("replay");
  };

  const keyword = confirmKind === "recall" ? "RECALL" : "ABORT";
  const acknowledged = confirmText.trim().toUpperCase() === keyword && reason.trim().length >= 3;

  const submitEmergency = async () => {
    if (!confirmKind || !acknowledged) return;
    const action = confirmKind;
    const result = await run(
      action === "recall" ? "Emergency recall" : "Abort mission",
      () => (action === "recall" ? api.recall(reason.trim()) : api.abort(reason.trim())),
      action === "recall" ? "Emergency recall commanded — fleet returning to pad" : "Mission aborted — aircraft commanded to hold",
    );
    if (result) setConfirmKind(null);
  };

  if (!booted) {
    return <BootScreen onEnter={enter} />;
  }

  if (!state) {
    return (
      <div style={{ height: "100%", display: "grid", placeItems: "center", background: "var(--bg)" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
          <Loader2 className="spin" size={26} style={{ color: "var(--accent)" }} />
          <div className="micro">{error ?? "Connecting to local ground service"}</div>
        </div>
      </div>
    );
  }

  const title = TITLES[screen];

  return (
    <div className="app-shell">
      <aside className="rail" aria-label="Primary navigation">
        <div className="rail-logo" title="RescueSwarm GCS">
          <Logo size={24} />
        </div>
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              className={clsx("rail-button", screen === item.id && "active")}
              onClick={() => setScreen(item.id)}
              aria-label={item.label}
              aria-current={screen === item.id ? "page" : undefined}
              title={item.label}
            >
              <Icon size={19} />
            </button>
          );
        })}
        <div className="rail-spacer" />
        <button
          className="rail-button"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          title={theme === "dark" ? "Use sunlight theme" : "Use night theme"}
          aria-label="Toggle colour theme"
        >
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </button>
        <div
          title={connected ? "Local GCS service connected" : "Local GCS service unavailable"}
          style={{ display: "grid", placeItems: "center", width: 30, height: 30, color: connected ? "var(--ok)" : "var(--danger)" }}
        >
          {connected ? <Wifi size={15} /> : <WifiOff size={15} />}
        </div>
      </aside>

      <div className="main-col">
        <header className="topbar">
          <div className="brand-block">
            <span className="brand-name">RescueSwarm</span>
            <span className="brand-sub">Ground Control · ADRODS-X</span>
          </div>

          <div className="topbar-title">
            <span className="micro">{title.sub}</span>
            <strong style={{ fontSize: 13, letterSpacing: "-.01em" }}>{title.title}</strong>
          </div>

          <div className="topbar-spacer" />

          <div className="topbar-actions">
            <Badge tone={mission?.mode === "REAL" ? "danger" : "accent"} dot pulse={mission?.mode === "SIMULATION"}>
              {mission?.mode === "REAL" ? "Live" : "Simulation"}
            </Badge>
            <Badge tone={phase === "EXECUTING" ? "info" : phase === "COMPLETE" ? "ok" : phase === "ABORTED" ? "danger" : "muted"}>
              {humanise(phase)}
            </Badge>
            <span className="fleet-dot" title={`${online} of ${vehicles.length} aircraft reporting fresh telemetry`}>
              <span className={clsx("pip", online === vehicles.length && online > 0 ? "ok" : online > 0 ? "warn" : "danger")} style={{ width: 5, height: 5, borderRadius: "50%" }} />
              {online}/{vehicles.length} link
            </span>
            <span className="mono" style={{ fontSize: 11, color: "var(--muted)", minWidth: 68, textAlign: "right" }} title="Local station time">
              {clock.toLocaleTimeString("en-GB", { hour12: false })}
            </span>

            {executing && (
              <>
                <button
                  className="btn danger sm"
                  onClick={() => setConfirmKind("abort")}
                  disabled={busy !== null}
                  title="Stop the mission and command every aircraft to hold"
                >
                  <TriangleAlert size={13} /> Abort
                </button>
                <button
                  className="btn emergency sm"
                  onClick={() => setConfirmKind("recall")}
                  disabled={busy !== null}
                  title="Command every aircraft to return to the launch pad"
                >
                  <RotateCcw size={13} /> Emergency recall
                </button>
              </>
            )}
          </div>
        </header>

        {!connected && (
          <div style={{ padding: "8px 22px 0" }}>
            <div className="offline-banner">
              <WifiOff size={14} />
              <span>
                Local ground service disconnected{error ? ` — ${error}` : ""}. Last state is shown for reference only;
                flight commands cannot be transmitted.
              </span>
              <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={() => void refresh()}>
                Retry
              </button>
            </div>
          </div>
        )}

        <div className="screen-host">
          {screen === "setup" && <SetupScreen onMission={() => setScreen("mission")} />}
          {screen === "mission" && <MissionScreen onOpenReplay={openReplay} />}
          {screen === "replay" && <ReplayScreen initialMissionId={replayMission} />}
          {screen === "logs" && <LogsScreen />}
          {screen === "settings" && <SettingsScreen />}
        </div>
      </div>

      <Modal
        open={confirmKind !== null}
        title={
          <span style={{ display: "inline-flex", alignItems: "center", gap: 9 }}>
            <ShieldAlert size={17} style={{ color: "var(--danger)" }} />
            {confirmKind === "recall" ? "Emergency recall" : "Abort mission"}
          </span>
        }
        onClose={() => setConfirmKind(null)}
        width={540}
        footer={
          <>
            <button className="btn ghost" onClick={() => setConfirmKind(null)}>Cancel</button>
            <button
              className={confirmKind === "recall" ? "btn emergency" : "btn danger"}
              disabled={!acknowledged || busy !== null}
              onClick={() => void submitEmergency()}
            >
              {busy ? <Loader2 size={14} className="spin" /> : confirmKind === "recall" ? <RotateCcw size={14} /> : <TriangleAlert size={14} />}
              {confirmKind === "recall" ? "Command recall" : "Abort and hold"}
            </button>
          </>
        }
      >
        <Notice tone="danger" icon={<ShieldAlert size={14} />}>
          {confirmKind === "recall"
            ? "Every aircraft is commanded to return to the launch pad. This does not disable ArduPilot failsafes, and the GCS closing cannot cancel an active recall."
            : "The mission ends as ABORTED and every aircraft is commanded to hold in LOITER. Payload tasks already in flight are recovered to RETURNING; no release is commanded by this action."}
        </Notice>
        <p className="small muted" style={{ lineHeight: 1.6, margin: "12px 0 6px" }}>
          Mode <strong>{mission?.mode}</strong> · mission <span className="mono">{mission?.mission_id ?? "—"}</span> ·
          phase <strong>{humanise(phase)}</strong>. The command, operator identity, reason and resulting state are written
          to the local evidence log.
        </p>
        <div className="field" style={{ marginTop: 10 }}>
          <label htmlFor="emergency-reason">Reason (logged)</label>
          <input
            id="emergency-reason"
            className="input"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={160}
            disabled={busy !== null}
          />
        </div>
        <div className="field" style={{ marginTop: 12 }}>
          <label htmlFor="emergency-confirm">
            Type {keyword} to confirm
          </label>
          <input
            id="emergency-confirm"
            className="input mono"
            value={confirmText}
            onChange={(event) => setConfirmText(event.target.value)}
            placeholder={keyword}
            autoFocus
            autoComplete="off"
            spellCheck={false}
            disabled={busy !== null}
          />
        </div>
      </Modal>

      <div className="toast-stack" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={clsx("toast", toast.kind)} role="status">
            <span className="bar" />
            <div>
              <div className="t">{toast.title}</div>
              {toast.detail && <div className="d">{toast.detail}</div>}
            </div>
            <button className="icon-button" style={{ width: 20, height: 20 }} onClick={() => dismissToast(toast.id)} aria-label="Dismiss notification">
              <X size={12} />
            </button>
          </div>
        ))}
        {loading && !toasts.length && (
          <div className="toast">
            <span className="bar" />
            <div className="t">Synchronising station state…</div>
            <span />
          </div>
        )}
      </div>
    </div>
  );
}
