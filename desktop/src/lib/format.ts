import type { GcsState } from "./types";

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
  // CONFIRMED means beam evidence closed the delivery, so it is the one delivery state
  // that may render as success; the ones below it are progress, not achievement.
  if (["DELIVERED", "CONFIRMED", "PASS", "LIVE", "RECORDING", "VERIFIED", "COMPLETE", "READY", "CONNECTED"].includes(s)) return "ok";
  if (["UNKNOWN", "UNSERVED", "WARNING", "MANUAL_PENDING", "STALE", "DEGRADED", "NO_SIGNAL", "FROZEN", "BLACK"].includes(s)) return "warn";
  if (["REJECTED", "FAIL", "OFFLINE", "LOST", "ABORTED", "DUPLICATE", "CRITICAL", "ERROR", "UNMAPPED", "CAPTURE_ERROR"].includes(s)) return "danger";
  if (["ASSIGNED", "IN_DELIVERY", "TRANSIT", "QUEUED", "LAUNCHING", "HOLD_CLEARANCE", "DESCENT",
      "HOVER_VERIFY", "ACK_PENDING", "RELEASE_SENT", "CLIMB", "RETURNING", "LANDING", "STANDBY",
      "EXECUTING", "SIMULATED"].includes(s)) return "info";
  return "muted";
}

/**
 * Provenance colour. The station labels every value with where it came from, and the
 * colour has to carry that distinction: a green tick next to "SIMULATED" would be worse
 * than no colour at all. Anything unrecognised is treated as unverified, never as benign.
 */
export function qualityTone(quality: string | null | undefined): "ok" | "warn" | "info" | "muted" {
  const q = (quality || "UNKNOWN").toUpperCase();
  if (["LIVE", "VERIFIED", "RECORDED", "COMMISSIONED", "PHYSICALLY_TESTED"].includes(q)) return "ok";
  if (["SIMULATED", "CALCULATED", "ESTIMATED", "DESIGNED", "ASSUMED"].includes(q)) return "info";
  if (["SYSTEM", "MANUAL", "PENDING_TEST", "NOT_MEASURED"].includes(q)) return "muted";
  return "warn";
}

/**
 * Command-lifecycle colour, kept deliberately separate from `statusTone`.
 *
 * ACKNOWLEDGED means "the autopilot answered ACCEPTED", which is a communication fact and
 * not evidence that the aircraft reached the commanded state — so it must never render in
 * success green. VERIFIED is the only state that means the vehicle actually did it.
 */
export function txTone(state: string): "ok" | "warn" | "danger" | "info" | "muted" {
  const s = state.toUpperCase();
  if (["VERIFIED", "COMPLETED"].includes(s)) return "ok";
  if (["REJECTED", "TIMEOUT", "FAILED"].includes(s)) return "danger";
  if (["ACK_UNVERIFIED", "ACKNOWLEDGED"].includes(s)) return "warn";
  if (["SENT", "RETRYING", "CREATED"].includes(s)) return "info";
  return "muted";
}

/**
 * The one-line honest gloss for a lifecycle state, shown next to the badge so an operator
 * reading at a glance cannot mistake an acceptance for a completion.
 */
export function txMeaning(state: string): string {
  const s = state.toUpperCase();
  if (s === "VERIFIED") return "vehicle state confirms it";
  if (s === "ACKNOWLEDGED") return "autopilot accepted · state not yet confirmed";
  if (s === "ACK_UNVERIFIED") return "accepted but the expected state was never seen";
  if (s === "TIMEOUT") return "no COMMAND_ACK · may not have been received";
  if (s === "REJECTED") return "autopilot refused the command";
  if (s === "RETRYING") return "unanswered · retrying";
  if (s === "SENT") return "transmitted · awaiting ACK";
  if (s === "CREATED") return "queued · not transmitted";
  return s.toLowerCase().replace(/_/g, " ");
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

/**
 * Provenance of the fleet data on screen, read from the telemetry itself rather than
 * asserted from the mode label: a REAL session with no heartbeats has no evidence, and a
 * mixed fleet (one aircraft linked, two lost) must not be reported as LIVE.
 *
 * Shared because the setup workflow and both mission-screen maps have to answer the same
 * question from the same field. Deriving it from `mission.mode` instead let a stale or
 * simulated read carry a "LIVE TELEMETRY" chip.
 */
export function evidenceQuality(state: GcsState | null): string {
  if (!state) return "NO TELEMETRY";
  const source = state.fleet.vehicles;
  const list = Array.isArray(source) ? source : Object.values(source ?? {});
  if (!list.length) return state.mission.mode === "SIMULATION" ? "SIMULATED" : "NO TELEMETRY";
  const qualities = Array.from(new Set(list.map((vehicle) => vehicle.data_quality || "UNKNOWN"))).sort();
  return qualities.length === 1 ? qualities[0] : `MIXED (${qualities.join(" / ")})`;
}
