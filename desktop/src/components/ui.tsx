import { type CSSProperties, type ReactNode, useEffect } from "react";
import clsx from "clsx";
import { X } from "lucide-react";

export function Badge({
  children,
  tone = "muted",
  dot = false,
  pulse = false,
  className,
  style,
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "danger" | "info" | "muted" | "accent";
  dot?: boolean;
  pulse?: boolean;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span className={clsx("badge", tone, pulse && "pulse", className)} style={style}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

export function Panel({
  title,
  icon,
  actions,
  children,
  className,
  bodyClass,
  flush,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
  flush?: boolean;
}) {
  return (
    <section className={clsx("panel", flush && "flush", className)}>
      {(title || actions) && (
        <header className="panel-head">
          <div className="panel-title">
            {icon}
            {title}
          </div>
          <div style={{ flex: 1 }} />
          {actions}
        </header>
      )}
      <div className={clsx("panel-body", bodyClass)}>{children}</div>
    </section>
  );
}

export function KV({ k, v, mono = true }: { k: ReactNode; v: ReactNode; mono?: boolean }) {
  return (
    <div className="kv">
      <span className="k">{k}</span>
      <span className={clsx("v", mono && "mono")}>{v}</span>
    </div>
  );
}

export function Meter({
  value,
  tone = "ok",
  label,
}: {
  value: number | null | undefined;
  tone?: "ok" | "warn" | "danger" | "accent" | "muted";
  label?: string;
}) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  const colour =
    tone === "danger" ? "var(--danger)" : tone === "warn" ? "var(--warn)" : tone === "accent" ? "var(--accent)" : tone === "muted" ? "var(--faint)" : "var(--ok)";
  return (
    <div style={{ width: "100%" }}>
      <div className="meter">
        <span style={{ width: `${pct}%`, background: colour }} />
      </div>
      {label && (
        <div className="micro" style={{ marginTop: 4, color: "var(--faint)" }}>
          {label}
        </div>
      )}
    </div>
  );
}

export function Stat({ value, label, sub }: { value: ReactNode; label: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <span className="value">{value}</span>
      <span className="label">{label}</span>
      {sub && <span className="small muted">{sub}</span>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Modal({
  open,
  title,
  children,
  footer,
  onClose,
  width,
}: {
  open: boolean;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : "Dialog"} style={width ? { width: `min(${width}px, 100%)` } : undefined}>
        <div className="modal-body">
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
            <h2 className="modal-title" style={{ flex: 1 }}>{title}</h2>
            <button className="icon-button" onClick={onClose} aria-label="Close dialog">
              <X size={15} />
            </button>
          </div>
          {children}
        </div>
        {footer && <div className="modal-actions">{footer}</div>}
      </div>
    </div>
  );
}

export function Notice({
  tone = "muted",
  icon,
  children,
}: {
  tone?: "muted" | "warn" | "danger" | "signal";
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={clsx("notice", tone !== "muted" && tone)}>
      {icon}
      <div>{children}</div>
    </div>
  );
}

export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="M16 2.4 27.6 9v14L16 29.6 4.4 23V9L16 2.4Z" stroke="currentColor" strokeWidth="1.5" opacity=".55" />
      <path d="M16 8.2v15.6M8.9 12.1l14.2 8.2M23.1 12.1 8.9 20.3" stroke="currentColor" strokeWidth="1.2" opacity=".45" />
      <circle cx="16" cy="16" r="3.2" fill="currentColor" />
      <circle cx="16" cy="6.2" r="1.5" fill="currentColor" opacity=".9" />
      <circle cx="24.2" cy="20.6" r="1.5" fill="currentColor" opacity=".9" />
      <circle cx="7.8" cy="20.6" r="1.5" fill="currentColor" opacity=".9" />
    </svg>
  );
}

export function AircraftGlyph({ role, colour = "var(--accent)" }: { role: "SEARCH" | "DELIVERY"; colour?: string }) {
  if (role === "SEARCH") {
    return (
      <svg className="aircraft-glyph" viewBox="0 0 56 44" fill="none" aria-hidden="true">
        <g stroke={colour} strokeWidth="1.3" opacity=".95">
          <rect x="20" y="15" width="16" height="14" rx="4" />
          <path d="M28 15V8M28 29v7M20 22h-9M36 22h9" />
          <circle cx="11" cy="12" r="5.5" opacity=".55" />
          <circle cx="45" cy="12" r="5.5" opacity=".55" />
          <circle cx="11" cy="32" r="5.5" opacity=".55" />
          <circle cx="45" cy="32" r="5.5" opacity=".55" />
        </g>
        <circle cx="28" cy="22" r="2" fill={colour} />
      </svg>
    );
  }
  return (
    <svg className="aircraft-glyph" viewBox="0 0 56 44" fill="none" aria-hidden="true">
      <g stroke={colour} strokeWidth="1.3" opacity=".95">
        <path d="M20 16h16l3 12H17l3-12Z" />
        <path d="M28 16V6M28 28v8M20 22l-11-7M36 22l11-7M20 22 9 29M36 22l11 7" />
        <circle cx="8" cy="14" r="4.6" opacity=".45" />
        <circle cx="48" cy="14" r="4.6" opacity=".45" />
        <circle cx="8" cy="30" r="4.6" opacity=".45" />
        <circle cx="48" cy="30" r="4.6" opacity=".45" />
        <circle cx="28" cy="5" r="4.6" opacity=".45" />
        <circle cx="28" cy="37" r="4.6" opacity=".45" />
      </g>
      <circle cx="28" cy="22" r="2" fill={colour} />
    </svg>
  );
}

export function ProgressRing({
  value,
  size = 34,
  stroke = 3,
  colour = "var(--accent)",
}: {
  value: number;
  size?: number;
  stroke?: number;
  colour?: string;
}) {
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - Math.max(0, Math.min(100, value)) / 100);
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={radius} stroke="var(--line)" strokeWidth={stroke} fill="none" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        stroke={colour}
        strokeWidth={stroke}
        fill="none"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dashoffset 400ms ease" }}
      />
    </svg>
  );
}
