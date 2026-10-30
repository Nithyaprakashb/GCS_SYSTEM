export type Mode = "SIMULATION" | "REAL";

export interface Vehicle {
  callsign: string;
  sysid: number;
  role: "SEARCH" | "DELIVERY";
  frame: string;
  connected: boolean;
  link: string;
  identity_valid: boolean;
  mode: string;
  armed: boolean;
  lat: number | null;
  lon: number | null;
  alt_rel_m: number | null;
  agl_m: number | null;
  groundspeed_ms: number | null;
  heading_deg: number | null;
  battery_pct: number | null;
  battery_voltage: number | null;
  gps_fix: number;
  gps_sats: number;
  ekf_ok: boolean | null;
  rangefinder_m: number | null;
  link_quality: number | null;
  rssi: number | null;
  heartbeat_age_s: number | null;
  stale: boolean;
  data_quality: string;
  task: string;
  failsafe: string | null;
  queue_depth: number;
  kits_free: number;
  search_alt_m: number;
  transit_alt_m: number;
  statustext: string;
}

export interface FleetSnapshot {
  mode: Mode | "OFFLINE";
  connected: boolean;
  vehicles: Record<string, Vehicle> | Vehicle[];
  topology?: {
    mode?: string;
    verified?: boolean;
    note?: string;
  };
  policy?: string;
}

export interface Survivor {
  survivor_id: string;
  lat: number;
  lon: number;
  drop_lat: number;
  drop_lon: number;
  status: "CONFIRMED" | "ASSIGNED" | "IN_DELIVERY" | "DELIVERED" | "UNKNOWN" | "UNSERVED" | "DUPLICATE" | "REJECTED";
  confidence?: number;
  observations?: number;
  owner?: string | null;
  kit_id?: string | null;
  eta_s?: number | null;
  first_seen?: string;
  last_seen?: string;
  source_aircraft?: string;
  data_quality?: string;
  note?: string | null;
}

export interface SurvivorSnapshot {
  survivors: Survivor[];
  counts: Record<string, number>;
  total: number;
  merge_distance_m: number;
  max_survivors: number;
}

export interface Kit {
  kit_id: string;
  callsign: string;
  load_status: string;
  final_status: string | null;
  reserved_for: string | null;
}

export interface KitSnapshot {
  kits: Kit[];
  counts: Record<string, number>;
  total: number;
  free?: Record<string, number>;
  policy?: string;
}

export interface Assignment {
  assignment_id: string;
  survivor_id: string;
  callsign: string;
  kit_id: string;
  status: string;
  eta_s: number;
  created_at: string;
  decision?: {
    rule?: string;
    candidates?: { callsign: string; eta_s: number; distance_m: number; free_kits: number }[];
    rejections?: { callsign: string; reasons: string[] }[];
    selected?: string;
  };
}

export interface AllocationSnapshot {
  assignments: Assignment[];
  counts: Record<string, number>;
  queues?: Record<string, string[]>;
  rule?: string;
}

export interface DeliveryTask {
  assignment_id: string;
  survivor_id: string;
  callsign: string;
  kit_id: string;
  state: string;
  beam_result?: string | null;
  history?: { state: string; at: string; detail: string }[];
}

export interface DeliverySnapshot {
  tasks: DeliveryTask[] | Record<string, DeliveryTask>;
  counts?: Record<string, number>;
  policy?: string;
}

export interface VideoFeed {
  feed_id: string;
  callsign: string;
  receiver: string;
  device_fingerprint?: string | null;
  status: string;
  mapped: boolean;
  source?: string;
  stream_url?: string;
  still_url?: string;
  resolution?: string;
  fps?: number | null;
  frames_received?: number;
  frame_age_ms?: number | null;
  latency_estimate_ms?: number | null;
  frozen_s?: number;
  black?: boolean;
  luma?: number | null;
  dropped?: number;
  recording: boolean;
  recording_path?: string | null;
  message?: string;
  note?: string;
}

export interface VideoHealth {
  mode: Mode;
  feeds: VideoFeed[];
  live_count: number;
  total: number;
  recording_count: number;
  policy?: string;
}

export interface SafetySnapshot {
  separations: { rule_id?: string; pair: string; horizontal_m: number; vertical_m: number | null; detail?: string }[];
  pad: {
    pad: string;
    lat: number;
    lon: number;
    occupants: { callsign: string }[];
    clear: boolean;
    violation: boolean;
    rule: string;
  };
  boundaries: Record<string, { available: boolean; distance_m?: number; nearest_m?: number; inside?: boolean; warning?: boolean; breach?: boolean }>;
}

export interface GcsEvent {
  event_id?: string;
  topic: string;
  severity: string;
  source: string;
  receive_time: string;
  data_quality: string;
  payload: Record<string, unknown>;
}

export interface PlanLane {
  [index: number]: { lat: number; lon: number };
  length?: number;
}

export interface MissionPlan {
  plan_id: string;
  plan_hash: string;
  created_at: string;
  aoi?: Aoi;
  search: {
    lane_count: number;
    lane_spacing_m?: number;
    usable_swath_m?: number;
    lane_length_m?: number;
    path_length_m: number;
    search_time_s?: number;
    lanes: { lat: number; lon: number }[][];
    long_axis_deg?: number;
    evidence?: string;
  };
  estimate?: {
    search_min?: number;
    total_min_low?: number;
    total_min_high?: number;
    label?: string;
    [key: string]: unknown;
  };
  energy?: Record<string, unknown>;
  geofences?: Record<string, Geofence>;
  pad?: { lat: number; lon: number; radius_m: number; name?: string; inside_aoi?: boolean; distance_to_boundary_m?: number; warnings?: string[] };
  validation?: { ok: boolean; errors?: string[]; warnings?: string[] };
  mission_items?: unknown[];
  delivery?: Record<string, unknown>;
}

export interface Geofence {
  callsign: string;
  polygon?: [number, number][];
  ring?: [number, number][];
  type?: string;
  [key: string]: unknown;
}

export interface Aoi {
  name?: string;
  ring: [number, number][];
  area_ha: number;
  perimeter_m?: number;
  centroid?: { lat: number; lon: number };
  valid?: boolean;
  warnings?: string[];
  source_hash?: string;
}

export interface MissionStatus {
  mission_id: string | null;
  name: string | null;
  mode: Mode;
  phase: string;
  outcome: string | null;
  started_at: string | null;
  ended_at: string | null;
  elapsed_s: number;
  recall: boolean;
  aoi: Aoi | null;
  plan: MissionPlan | null;
  upload: { verified?: boolean; aircraft?: Record<string, UploadReceipt> };
  upload_verified: boolean;
  readiness: { ready: boolean | null; counts: Record<string, number>; blocking: string[] };
  simulation?: {
    running: boolean;
    sim_time_s: number;
    speed: number;
    recall: boolean;
    aircraft?: { callsign: string; phase: string }[];
  } | null;
  evidence: string;
}

export interface UploadReceipt {
  status: string;
  simulated?: boolean;
  items_uploaded?: number;
  items_read_back?: number;
  error?: string;
  [key: string]: unknown;
}

export interface PreflightCheck {
  check_id: string;
  scope: string;
  title: string;
  result: "PASS" | "FAIL" | "UNKNOWN" | "MANUAL_PENDING";
  measured?: string | number | null;
  threshold?: string | null;
  detail?: string;
  blocking?: boolean;
  evidence_source?: string;
}

export interface PreflightReport {
  ready: boolean;
  checks: PreflightCheck[];
  counts: Record<string, number>;
  blocking: PreflightCheck[];
  generated_at?: string;
  policy?: string;
}

export interface ManualItem {
  check_id: string;
  scope: string;
  title: string;
  detail: string;
  signed: boolean;
  signed_by?: string;
  signed_at?: string;
}

export interface GcsState {
  app: {
    version: string;
    started_at: string;
    mode: Mode;
    telemetry_mode: string;
    offline: boolean;
    data_dir: string;
  };
  mission: MissionStatus;
  fleet: FleetSnapshot;
  safety: SafetySnapshot;
  survivors: SurvivorSnapshot;
  kits: KitSnapshot;
  allocations: AllocationSnapshot;
  deliveries: DeliverySnapshot;
  video: VideoHealth;
  preflight: { manual: ManualItem[]; last: PreflightReport | null };
  events: GcsEvent[];
  generated_at: string;
}

export interface MissionListEntry {
  mission_id: string;
  name: string;
  mode: Mode;
  phase: string;
  state: string;
  created_at: string;
  started_at?: string;
  ended_at?: string;
  outcome?: string;
  plan_hash?: string;
}
