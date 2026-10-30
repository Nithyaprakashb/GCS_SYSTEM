export function fmtTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export function fmtNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtDistance(metres: number | null | undefined): string {
  if (metres === null || metres === undefined || Number.isNaN(metres)) return "—";
  return metres >= 1000 ? `${(metres / 1000).toFixed(2)} km` : `${Math.round(metres)} m`;
}

export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-IN", { hour12: false });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function fmtCoords(lat: number | null | undefined, lon: number | null | undefined): string {
  if (lat === null || lon === null || lat === undefined || lon === undefined) return "—";
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

export function statusTone(status: string): "ok" | "warn" | "danger" | "info" | "muted" {
  const s = status.toUpperCase();
  if (["DELIVERED", "PASS", "LIVE", "RECORDING", "VERIFIED", "COMPLETE", "READY", "CONNECTED"].includes(s)) return "ok";
  if (["UNKNOWN", "UNSERVED", "WARNING", "MANUAL_PENDING", "STALE", "DEGRADED"].includes(s)) return "warn";
  if (["REJECTED", "FAIL", "OFFLINE", "LOST", "ABORTED", "DUPLICATE", "CRITICAL"].includes(s)) return "danger";
  if (["ASSIGNED", "IN_DELIVERY", "TRANSIT", "QUEUED", "LAUNCHING", "RELEASE_SENT", "EXECUTING", "SIMULATED"].includes(s)) return "info";
  return "muted";
}

export function humanise(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function batteryTone(pct: number | null | undefined): "ok" | "warn" | "danger" | "muted" {
  if (pct === null || pct === undefined) return "muted";
  if (pct <= 22) return "danger";
  if (pct <= 40) return "warn";
  return "ok";
}
