import { useEffect, useState } from "react";
import { ArrowRight, Check, Loader2, ShieldCheck, TriangleAlert, X } from "lucide-react";
import { api } from "../lib/api";
import { Logo } from "../components/ui";

type CheckState = "pending" | "ok" | "warn" | "fail";

interface StartupCheck {
  id: string;
  label: string;
  state: CheckState;
  detail: string;
}

const BASE_CHECKS: StartupCheck[] = [
  { id: "backend", label: "Ground control process", state: "pending", detail: "Starting local service" },
  { id: "database", label: "Mission evidence database", state: "pending", detail: "SQLite journal" },
  { id: "storage", label: "Recording storage", state: "pending", detail: "Local evidence volume" },
  { id: "map", label: "Offline tactical basemap", state: "pending", detail: "Procedural tiles · no internet" },
  { id: "video", label: "Video pipeline", state: "pending", detail: "Three analog channels" },
  { id: "safety", label: "Independent safety engine", state: "pending", detail: "Deterministic interlocks" },
];

export function BootScreen({ onEnter }: { onEnter: () => void }) {
  const [checks, setChecks] = useState<StartupCheck[]>(BASE_CHECKS);
  const [version, setVersion] = useState("2.0.0");
  const [platform, setPlatform] = useState("Windows · local");
  const [ready, setReady] = useState(false);
  const [fatal, setFatal] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const health = await api.health();
        if (cancelled) return;
        setVersion(health.version);
        const diagnostics = await api.diagnostics();
        if (cancelled) return;
        const env = (diagnostics.environment ?? {}) as Record<string, string>;
        setPlatform(`${env.platform ?? "Local"} · Python ${env.python ?? "?"}`);
        const mapped = BASE_CHECKS.map((check) => {
          if (check.id === "video") {
            const item = (diagnostics.checks as { check_id: string; status: string; detail: string }[] | undefined)?.find(
              (entry) => entry.check_id === "video_service",
            );
            return {
              ...check,
              state: (item?.status === "PASS" ? "ok" : item?.status === "WARN" ? "warn" : "fail") as CheckState,
              detail: item?.detail ?? check.detail,
            };
          }
          if (check.id === "map" || check.id === "safety" || check.id === "database" || check.id === "storage" || check.id === "backend") {
            const item = (diagnostics.checks as { check_id: string; status: string; detail: string }[] | undefined)?.find(
              (entry) => entry.check_id === check.id || (check.id === "video" && entry.check_id === "video_service"),
            );
            if (item) {
              return {
                ...check,
                state: (item.status === "PASS" ? "ok" : item.status === "WARN" ? "warn" : "fail") as CheckState,
                detail: item.detail,
              };
            }
          }
          return { ...check, state: "warn" as CheckState, detail: "Not reported by this backend build" };
        });
        setChecks(mapped);
        const failed = mapped.some((check) => check.state === "fail");
        setReady(!failed);
        setFatal(failed ? mapped.find((check) => check.state === "fail")?.detail ?? "Startup check failed" : null);
      } catch (error) {
        if (cancelled) return;
        setChecks((current) =>
          current.map((check) =>
            check.id === "backend"
              ? { ...check, state: "fail", detail: "Backend unreachable on 127.0.0.1:8765" }
              : check,
          ),
        );
        setFatal(error instanceof Error ? error.message : "Backend unreachable");
        setReady(false);
      }
    };
    void run();
    const timer = window.setInterval(() => void run(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const backendFailed = checks.some((check) => check.id === "backend" && check.state === "fail");

  return (
    <div className="splash">
      <div className="splash-visual">
        <div className="radar">
          <div className="radar-sweep" />
          <span className="radar-blip" style={{ left: "63%", top: "38%" }} />
          <span className="radar-blip b2" style={{ left: "38%", top: "62%" }} />
          <span className="radar-blip b3" style={{ left: "52%", top: "31%" }} />
          <div className="radar-core">
            <div style={{ color: "var(--accent)" }}>
              <Logo size={74} />
            </div>
          </div>
        </div>
        <div style={{ position: "absolute", left: 34, bottom: 30, display: "flex", alignItems: "center", gap: 9, color: "#5f728b", fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase" }}>
          <ShieldCheck size={13} /> No cloud · No internet · Local authority
        </div>
      </div>
      <div className="splash-content">
        <div className="splash-eyebrow">
          <span style={{ width: 18, height: 1, background: "var(--signal)" }} />
          Team ADRODS-X · NIDAR 2026–27
        </div>
        <div>
          <h1 className="splash-title">
            Rescue<span className="accent">Swarm</span>
            <br />
            Ground Control
          </h1>
          <p className="splash-desc">
            Autonomous aerial search and precision payload delivery. Three aircraft, one shared kit ledger,
            deterministic safety and a complete local evidence trail.
          </p>
        </div>
        <div className="splash-checks">
          {checks.map((check) => (
            <div className="splash-check" key={check.id}>
              {check.state === "pending" && <Loader2 size={13} className="spin" style={{ animation: "spin .9s linear infinite" }} />}
              {check.state === "ok" && <Check size={13} style={{ color: "var(--ok)" }} />}
              {/* A tick in amber still reads as "passed". warn and unverified need a
                  different glyph, not just a different colour. */}
              {check.state === "warn" && <TriangleAlert size={13} style={{ color: "var(--warn)" }} />}
              {check.state === "fail" && <X size={13} style={{ color: "var(--danger)" }} />}
              <span style={{ color: check.state === "fail" ? "var(--danger)" : undefined }}>{check.label}</span>
              <span className={`state ${check.state === "ok" ? "ok" : check.state === "warn" ? "warn" : ""}`} style={{ maxWidth: 210, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={check.detail}>
                {check.state === "pending" ? "checking" : check.state === "fail" ? "failed" : check.detail}
              </span>
            </div>
          ))}
        </div>
        {fatal && (
          <div className="notice danger" style={{ background: "rgba(239,95,99,.09)", color: "#ef8f92" }}>
            <X size={14} />
            <div>
              <strong>{backendFailed ? "Ground service unreachable." : "Startup check failed."}</strong> {fatal}.
              {backendFailed
                ? <> Start it with <span className="mono">python -m rescueswarm.main</span> — this screen re-checks every 4 s on its own.</>
                : " The station will not authorise launch until this clears."}
            </div>
          </div>
        )}
        <div className="splash-actions">
          <button className="splash-enter" onClick={onEnter} disabled={!ready}>
            Enter control station <ArrowRight size={16} />
          </button>
          <span className="mono" style={{ fontSize: 10, color: "#5f728b", letterSpacing: ".06em" }}>
            GCS v{version}
          </span>
        </div>
        <div className="splash-meta">
          <span>Problem Statement 1</span>
          <span>10 ha · 10 survivors</span>
          <span>{platform}</span>
        </div>
      </div>
    </div>
  );
}
