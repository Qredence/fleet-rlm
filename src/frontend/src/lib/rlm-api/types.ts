import type { components, paths } from "@/lib/rlm-api/generated/openapi";

export type OpenApiPaths = paths;

export type HealthResponse = components["schemas"]["HealthResponse"];
export type ReadyResponse = components["schemas"]["ReadyResponse"];
export type AuthLoginResponse = components["schemas"]["AuthLoginResponse"];
export type AuthLogoutResponse = components["schemas"]["AuthLogoutResponse"];
export type AuthMeResponse = components["schemas"]["AuthMeResponse"];
export type SessionStateResponse =
  components["schemas"]["SessionStateResponse"];
export type SessionStateSummary = components["schemas"]["SessionStateSummary"];

export type RuntimeSettingsSnapshot =
  components["schemas"]["RuntimeSettingsSnapshot"];
export type RuntimeSettingsUpdateResponse =
  components["schemas"]["RuntimeSettingsUpdateResponse"];
export type RuntimeConnectivityTestKind =
  components["schemas"]["RuntimeConnectivityTestResponse"]["kind"];
export type RuntimeConnectivityTestResponse =
  components["schemas"]["RuntimeConnectivityTestResponse"];
export type RuntimeTestCache = components["schemas"]["RuntimeTestCache"];
export type RuntimeStatusResponse =
  components["schemas"]["RuntimeStatusResponse"];

// ── Legacy REST Shapes (for compatibility adapters/fallback paths) ──

export interface ApiTask {
  id: string;
  name: string;
  display_name: string;
  version: string;
  domain: string;
  category: string;
  status: "draft" | "validating" | "validated" | "published" | "deprecated";
  description: string;
  tags: string[];
  dependencies: string[];
  taxonomy_path: string;
  usage_count: number;
  last_used: string;
  quality_score: number;
  author: string;
  created_at: string;
  updated_at?: string;
}

export interface ApiTaskListResponse {
  items: ApiTask[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface ApiTaxonomyNode {
  id: string;
  name: string;
  path: string;
  children: ApiTaxonomyNode[];
  skill_count: number;
  skills?: string[];
}

export interface ApiAnalytics {
  total_skills: number;
  active_skills: number;
  total_executions: number;
  avg_quality_score: number;
  weekly_growth: number;
  executions_by_day: { date: string; count: number }[];
  top_skills: { name: string; uses: number }[];
  quality_dist: { range: string; count: number }[];
}

export interface ApiUserProfile {
  id: string;
  name: string;
  email: string;
  initials: string;
  avatar_url?: string;
  role: string;
  plan: "free" | "pro" | "enterprise";
  org: string;
}

export interface ApiChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  message_type?:
    | "text"
    | "hitl"
    | "clarification"
    | "reasoning"
    | "plan"
    | "artifact";
  phase?: number;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface ApiMemoryEntry {
  id: string;
  type: "fact" | "preference" | "session" | "knowledge" | "directive";
  content: string;
  source: string;
  created_at: string;
  updated_at: string;
  relevance: number;
  tags: string[];
  session_id?: string;
  pinned?: boolean;
}

export interface ApiMemoryListResponse {
  items: ApiMemoryEntry[];
  total: number;
}

export interface ApiFsNode {
  id: string;
  name: string;
  path: string;
  type: "volume" | "directory" | "file";
  children?: ApiFsNode[];
  size?: number;
  mime?: string;
  modified_at?: string;
  skill_id?: string;
}
