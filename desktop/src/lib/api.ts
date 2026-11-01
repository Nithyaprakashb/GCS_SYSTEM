import type {
  AllocationSnapshot,
  DeliverySnapshot,
  GcsState,
  KitSnapshot,
  ManualItem,
  MissionListEntry,
  MissionPlan,
  MissionStatus,
  PreflightReport,
  SurvivorSnapshot,
  VideoHealth,
} from "./types";

/**
 * Local API client.
 *
 * Two rules the rest of the station depends on:
 *  - every request carries a deadline. A hung loopback call must show up as an error
 *    the operator can act on, not as a spinner that outlives the sortie;
 *  - the operator identity travels in X-Operator so the evidence log records who gave
 *    the order. Outside Electron (vite dev server) there is no shell user, so a stable
 *    development name is used and the backend still attributes the action.
 */

const shell = (window as unknown as {
  rescueswarmShell?: { runtimeInfo?: () => Promise<{ operator?: string | null }> };
}).rescueswarmShell;

let operatorName = "GCS-OPERATOR";
void shell
  ?.runtimeInfo?.()
  .then((info) => {
    const name = typeof info?.operator === "string" ? info.operator.trim() : "";
    if (name) operatorName = name.slice(0, 64);
  })
  .catch(() => {
    /* The default name remains; commands are still attributed, just less specifically. */
  });

const DEFAULT_TIMEOUT_MS = 8_000;

function timeoutSignal(timeoutMs: number): AbortSignal | undefined {
  const factory = AbortSignal as unknown as { timeout?: (ms: number) => AbortSignal };
  return typeof factory.timeout === "function" ? factory.timeout(timeoutMs) : undefined;
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const headers = {
    "Content-Type": "application/json",
    "X-Operator": operatorName,
    ...(init?.headers ?? {}),
  };
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...init, headers, signal: timeoutSignal(timeoutMs) });
  } catch (err) {
    const name = err instanceof Error ? err.name : "";
    if (name === "TimeoutError") {
      throw new Error(`Local service did not answer within ${Math.round(timeoutMs / 1000)} s (${path})`);
    }
    if (name === "AbortError") {
      throw new Error(`Request cancelled (${path})`);
    }
    throw new Error("Local ground service unreachable — the station backend is not answering");
  }
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown, timeoutMs?: number) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }, timeoutMs);

export const api = {
  health: () => request<{ ok: boolean; version: string; mode: string; phase: string }>("/health"),
  state: () => request<GcsState>("/state"),
  systemInfo: () => request<Record<string, unknown>>("/system/info", undefined, 20_000),
  diagnostics: () => request<Record<string, unknown>>("/system/diagnostics", undefined, 45_000),
  devices: () => request<Record<string, unknown>>("/system/devices", undefined, 45_000),
  config: () => request<Record<string, unknown>>("/system/config"),
  updateConfig: (patch: Record<string, unknown>) =>
    request<Record<string, unknown>>("/system/config", {
      method: "PATCH",
      body: JSON.stringify({ patch }),
    }, 15_000),
  setMode: (mode: string) => post<{ mode: string }>("/system/mode", { mode }, 30_000),
  connect: (mode: string) => post<Record<string, unknown>>("/fleet/connect", { mode }, 60_000),
  disconnect: () => post<Record<string, unknown>>("/fleet/disconnect", undefined, 20_000),
  importKml: (payload: { kml_text?: string; filename?: string; sample?: boolean }) =>
    post<MissionStatus>("/mission/import-kml", payload, 30_000),
  loadSample: () => post<MissionStatus>("/mission/load-sample", undefined, 30_000),
  plan: () => post<MissionStatus>("/mission/plan", undefined, 45_000),
  preflight: () => post<PreflightReport>("/mission/preflight", undefined, 45_000),
  gate: () => request<Record<string, unknown>>("/mission/gate"),
  sign: (checkId: string, signed: boolean) => post<unknown>("/mission/sign", { check_id: checkId, signed }),
  signAllSimulation: () => post<unknown>("/mission/sign-all-sim", undefined, 20_000),
  // Three aircraft in series, each with an upload, a read-back and a parameter pass.
  // Worst configured case is 48 s per vehicle, so the UI must not give up first.
  upload: () => post<{ verified: boolean; receipts: Record<string, unknown> }>("/mission/upload", undefined, 200_000),
  start: (confirmation: string) => post<MissionStatus>("/mission/start", { confirmation }, 30_000),
  newMission: () => post<MissionStatus>("/mission/reset", undefined, 20_000),
  abort: (reason: string) => post<unknown>("/mission/abort", { reason }, 60_000),
  recall: (reason: string) => post<unknown>("/mission/recall", { reason }, 60_000),
  clearRecall: () => post<unknown>("/mission/recall/clear", undefined, 30_000),
  survivors: () => request<SurvivorSnapshot>("/survivors"),
  kits: () => request<KitSnapshot>("/kits"),
  allocations: () => request<AllocationSnapshot>("/allocations"),
  deliveries: () => request<DeliverySnapshot>("/deliveries"),
  videoHealth: () => request<VideoHealth>("/video/health"),
  videoStart: (mode?: string) => post<VideoHealth>("/video/start", mode ? { mode } : undefined, 30_000),
  videoStop: () => post<VideoHealth>("/video/stop", undefined, 30_000),
  inject: (fault: string, value?: unknown, callsign?: string) =>
    post<unknown>("/simulation/inject", { fault, value, callsign }),
  videoFault: (callsign: string, fault: string | null) =>
    post<unknown>(`/video/${callsign}/inject`, { fault }),
  copilot: (question: string) => post<{ answer: string; citations?: string[]; mode?: string }>("/copilot/ask", { question }, 75_000),
  missions: () => request<{ missions: MissionListEntry[] }>("/missions"),
  mission: (id: string) => request<Record<string, unknown>>(`/missions/${id}`),
  replay: (id: string) => request<ReplayPayload>(`/missions/${id}/replay`, undefined, 30_000),
  analytics: (id: string) => request<Record<string, unknown>>(`/missions/${id}/analytics`, undefined, 30_000),
  regenerateReport: (id: string) => post<unknown>(`/missions/${id}/report`, undefined, 60_000),
  manualItems: () => request<{ items: ManualItem[] }>("/mission/manual-items"),
  shutdownStation: (reason: string) =>
    post<{ shutting_down: boolean; services_stopped: Record<string, unknown> }>(
      "/system/shutdown", { confirmation: "SHUTDOWN", reason }, 30_000),
};

export interface ReplayPayload {
  mission: MissionListEntry;
  plan: MissionPlan | null;
  survivors: import("./types").Survivor[];
  assignments: import("./types").Assignment[];
  kits: import("./types").Kit[];
  tracks: Record<string, ReplaySample[]>;
  timeline: { t: string; topic: string; severity: string; source: string; data_quality: string; summary: string }[];
  markers: {
    survivor_id: string;
    lat: number;
    lon: number;
    drop_lat: number;
    drop_lon: number;
    status: string;
    owner?: string;
    kit_id?: string;
    first_seen?: string;
    data_quality?: string;
  }[];
  counts: { telemetry_samples: number; events: number; survivors: number };
  evidence: string;
}

export interface ReplaySample {
  t: string;
  lat: number;
  lon: number;
  alt_rel_m: number;
  agl_m: number;
  speed_ms: number;
  heading_deg: number;
  battery_pct: number;
  mode: string;
  armed: boolean;
  quality: string;
}

export function wsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}
