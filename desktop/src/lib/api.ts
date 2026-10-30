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

const OPERATOR = "GCS-OPERATOR";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: {
      "Content-Type": "application/json",
      "X-Operator": OPERATOR,
      ...(init?.headers ?? {}),
    },
    ...init,
  });
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

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<{ ok: boolean; version: string; mode: string; phase: string }>("/health"),
  state: () => request<GcsState>("/state"),
  systemInfo: () => request<Record<string, unknown>>("/system/info"),
  diagnostics: () => request<Record<string, unknown>>("/system/diagnostics"),
  devices: () => request<Record<string, unknown>>("/system/devices"),
  config: () => request<Record<string, unknown>>("/system/config"),
  updateConfig: (patch: Record<string, unknown>) =>
    request<Record<string, unknown>>("/system/config", {
      method: "PATCH",
      body: JSON.stringify({ patch }),
    }),
  setMode: (mode: string) => post<{ mode: string }>("/system/mode", { mode }),
  connect: (mode: string) => post<Record<string, unknown>>("/fleet/connect", { mode }),
  disconnect: () => post<Record<string, unknown>>("/fleet/disconnect"),
  importKml: (payload: { kml_text?: string; filename?: string; sample?: boolean }) =>
    post<MissionStatus>("/mission/import-kml", payload),
  loadSample: () => post<MissionStatus>("/mission/load-sample"),
  plan: () => post<MissionStatus>("/mission/plan"),
  preflight: () => post<PreflightReport>("/mission/preflight"),
  gate: () => request<Record<string, unknown>>("/mission/gate"),
  sign: (checkId: string, signed: boolean) => post<unknown>("/mission/sign", { check_id: checkId, signed }),
  signAllSimulation: () => post<unknown>("/mission/sign-all-sim"),
  upload: () => post<{ verified: boolean; receipts: Record<string, unknown> }>("/mission/upload"),
  start: (confirmation: string) => post<MissionStatus>("/mission/start", { confirmation }),
  newMission: () => post<MissionStatus>("/mission/reset"),
  abort: (reason: string) => post<unknown>("/mission/abort", { reason }),
  recall: (reason: string) => post<unknown>("/mission/recall", { reason }),
  clearRecall: () => post<unknown>("/mission/recall/clear"),
  survivors: () => request<SurvivorSnapshot>("/survivors"),
  kits: () => request<KitSnapshot>("/kits"),
  allocations: () => request<AllocationSnapshot>("/allocations"),
  deliveries: () => request<DeliverySnapshot>("/deliveries"),
  videoHealth: () => request<VideoHealth>("/video/health"),
  videoStart: (mode?: string) => post<VideoHealth>("/video/start", mode ? { mode } : undefined),
  inject: (fault: string, value?: unknown, callsign?: string) =>
    post<unknown>("/simulation/inject", { fault, value, callsign }),
  videoFault: (callsign: string, fault: string | null) =>
    post<unknown>(`/video/${callsign}/inject`, { fault }),
  copilot: (question: string) => post<{ answer: string; citations?: string[]; mode?: string }>("/copilot/ask", { question }),
  missions: () => request<{ missions: MissionListEntry[] }>("/missions"),
  mission: (id: string) => request<Record<string, unknown>>(`/missions/${id}`),
  replay: (id: string) => request<ReplayPayload>(`/missions/${id}/replay`),
  analytics: (id: string) => request<Record<string, unknown>>(`/missions/${id}/analytics`),
  regenerateReport: (id: string) => post<unknown>(`/missions/${id}/report`),
  manualItems: () => request<{ items: ManualItem[] }>("/mission/manual-items"),
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
