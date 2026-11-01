import { type CSSProperties, type ReactNode, useEffect, useId, useRef } from "react";
import clsx from "clsx";
import { X } from "lucide-react";

export function Badge({
  children,
  tone = "muted",
  dot = false,
  pulse = false,
  className,
  style,
  title,
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "danger" | "info" | "muted" | "accent";
  dot?: boolean;
  pulse?: boolean;
  className?: string;
  style?: CSSProperties;
  title?: string;
}) {
  return (
    <span className={clsx("badge", tone, pulse && "pulse", className)} style={style} title={title}>
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

/*
 * One Escape listener for the whole app, routed to the topmost open dialog. Modals can
 * nest — the abort confirmation sits above the expanded map modal — and when every mounted
 * Modal registers its own window listener, one keypress closes both and the operator loses
 * the dialog they did not mean to dismiss.
 */
const openDialogs: symbol[] = [];

export function Modal({
  open,
  title,
  children,
  footer,
  onClose,
  width,
  ariaLabel,
  blocking = false,
}: {
  open: boolean;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  width?: number;
  /** Accessible name for screens whose title is JSX (an icon plus text). */
  ariaLabel?: string;
  /** Destructive confirmations must not close on a stray press. */
  blocking?: boolean;
}) {
  const titleId = useId();
  const bodyId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  /*
   * The close handler is read through a ref because the effect below deliberately runs
   * only when `open` flips. Most callers pass an inline arrow, so a `onClose` dependency
   * would re-register the listener, re-capture focus and re-restore focus on every parent
   * render — and the shell re-renders once a second for the station clock.
   */
  const latestClose = useRef(onClose);
  latestClose.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    const token = Symbol("dialog");
    openDialogs.push(token);
    const handler = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (openDialogs[openDialogs.length - 1] !== token) return;
      event.stopPropagation();
      latestClose.current();
    };
    window.addEventListener("keydown", handler);
    /*
     * A dialog that swallows Tab lets focus walk straight out of the confirmation and into
     * the live controls behind it. The abort/recall dialog is the one where an operator
     * types a destructive token, so focus is kept inside and handed back to whatever was
     * focused before it opened.
     */
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusables = () => Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input:not([disabled]), select, [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
    const timer = window.setTimeout(() => {
      const targets = focusables();
      (targets.find((node) => node instanceof HTMLInputElement && !node.disabled)
        ?? targets[0]
        ?? panelRef.current)?.focus();
    }, 0);
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || openDialogs[openDialogs.length - 1] !== token) return;
      const targets = focusables();
      if (!targets.length) {
        event.preventDefault();
        return;
      }
      const first = targets[0];
      const last = targets[targets.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === panelRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", trap);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", handler);
      window.removeEventListener("keydown", trap);
      const index = openDialogs.indexOf(token);
      if (index >= 0) openDialogs.splice(index, 1);
      previouslyFocused?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  const namedById = typeof title === "string";
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => !blocking && event.target === event.currentTarget && onClose()}
    >
      <div
        ref={panelRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        aria-labelledby={namedById ? titleId : undefined}
        aria-label={namedById ? undefined : (ariaLabel ?? "Confirmation")}
        aria-describedby={bodyId}
        style={width ? { width: `min(${width}px, 100%)` } : undefined}
      >
        <div className="modal-body">
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
            <h2 id={namedById ? titleId : undefined} className="modal-title" style={{ flex: 1 }}>{title}</h2>
            <button className="icon-button" onClick={onClose} aria-label="Close dialog">
              <X size={15} />
            </button>
          </div>
          <div id={bodyId}>{children}</div>
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
