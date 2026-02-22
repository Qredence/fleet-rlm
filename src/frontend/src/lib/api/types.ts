/**
 * Backend response types for the fleet-rlm API.
 *
 * These types represent the **raw JSON shapes** returned by the backend
 * (snake_case fields). They are NOT used directly by UI components —
 * the adapter layer (`adapters.ts`) transforms them into the
 * frontend types defined in `components/data/types.ts`.
 *
 * ────────────────────────────────────────────────────────────────────
 * IMPORTANT: These types are inferred from the fleet-rlm repo structure:
 *   - src/fleet_rlm/server/schemas.py
 *   - src/fleet_rlm/server/routers/tasks.py
 *   - src/fleet_rlm/server/routers/sessions.py
 *   - src/fleet_rlm/server/routers/chat.py
 *   - src/fleet_rlm/server/routers/ws.py
 *
 * TODO: Validate these against the actual Pydantic schemas once the
 *       backend OpenAPI spec is available. Adjust field names, types,
 *       and optional markers as needed.
 *
 * ── Phase D Cross-Reference Checklist ──────────────────────────────
 *
 * STATUS: Blocked on backend OpenAPI spec / Pydantic schema access.
 *         Last audited: 2026-02-18
 *
 * When the spec is available, verify each interface against the actual
 * Pydantic model. The known gaps and assumptions are:
 *
 * 1. ApiTask
 *    - Assumed fields: `display_name`, `taxonomy_path`, `usage_count`,
 *      `quality_score`, `last_used`, `author`. Verify these exist.
 *    - `status` enum values: verify against the backend TaskStatus enum.
 *    - The frontend `Skill` type drops `updated_at` — add it if needed.
 *
 * 2. ApiChatMessage
 *    - `message_type` enum values are assumed from router inspection.
 *      Verify: 'text' | 'hitl' | 'clarification' | 'reasoning' | 'plan' | 'artifact'.
 *    - `metadata` structure for HITL, clarification, and reasoning payloads
 *      is unknown — the adapter stubs these as `undefined`. This is the
 *      HIGHEST PRIORITY item for Phase D: extracting structured sub-objects
 *      from `metadata` into `hitlData`, `clarificationData`, `reasoningData`.
 *    - `created_at` and `session_id` are dropped by the adapter — add to
 *      frontend ChatMessage type if UI needs them.
 *
 * 3. ApiStreamEvent
 *    - Event type enum and `data` shape are inferred from ws.py / chat.py.
 *      Verify the exact SSE event names and payload structures.
 *
 * 4. ApiAnalytics
 *    - Nested array shapes (`executions_by_day`, `top_skills`, `quality_dist`)
 *      are assumed — verify field names in each sub-object.
 *
 * 5. ApiMemoryEntry — looks well-aligned with types.ts. Verify `pinned`
 *    is a top-level field (not nested in metadata).
 *
 * 6. ApiFsNode — verify `skill_id` mapping and `mime` field presence.
 *
 * 7. Endpoint paths (endpoints.ts)
 *    - All paths assume `/api/v1/` prefix. Verify router mount points.
 *    - Bulk memory endpoints (`/bulk-pin`, `/bulk-delete`) may use
 *      different HTTP methods or URL structures.
 *
 * After verification, remove this checklist and the TODO markers below.
 * ────────────────────────────────────────────────────────────────────
 */

// ── Task (maps to frontend "Skill") ─────────────────────────────────

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
  last_used: string; // ISO date string
  quality_score: number;
  author: string;
  created_at: string; // ISO date string
  updated_at?: string; // ISO date string
}

export interface ApiTaskCreate {
  name: string;
  display_name: string;
  domain: string;
  category: string;
  description: string;
  tags?: string[];
  dependencies?: string[];
  taxonomy_path?: string;
}

export interface ApiTaskUpdate {
  display_name?: string;
  description?: string;
  status?: ApiTask["status"];
  tags?: string[];
  dependencies?: string[];
  taxonomy_path?: string;
  version?: string;
}

// ── Task List Response ──────────────────────────────────────────────

export interface ApiTaskListResponse {
  items: ApiTask[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

// ── Taxonomy ────────────────────────────────────────────────────────

export interface ApiTaxonomyNode {
  id: string;
  name: string;
  path: string;
  children: ApiTaxonomyNode[];
  skill_count: number;
  skills?: string[]; // task/skill IDs
}

// ── Session ─────────────────────────────────────────────────────────

export interface ApiSession {
  id: string;
  user_id: string;
  title?: string;
  created_at: string;
  updated_at: string;
  status: "active" | "completed" | "archived";
  metadata?: Record<string, unknown>;
}

export interface ApiSessionCreate {
  title?: string;
  metadata?: Record<string, unknown>;
}

// ── Chat ────────────────────────────────────────────────────────────

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

export interface ApiChatRequest {
  session_id: string;
  message: string;
  context?: Record<string, unknown>;
}

export interface ApiChatResponse {
  message: ApiChatMessage;
  session_id: string;
  /** When streaming, this field may contain partial content */
  is_complete: boolean;
}

// ── Chat Streaming Events (SSE / WebSocket) ─────────────────────────

export type ApiStreamEventType =
  | "message_start"
  | "content_delta"
  | "content_complete"
  | "thinking_start"
  | "thinking_delta"
  | "thinking_complete"
  | "hitl_request"
  | "clarification_request"
  | "plan_update"
  | "phase_change"
  | "artifact"
  | "error"
  | "done";

export interface ApiStreamEvent {
  event: ApiStreamEventType;
  data: Record<string, unknown>;
  /** Phase of the creation flow (1=understanding, 2=generating, 3=validating) */
  phase?: number;
}

// ── Analytics ───────────────────────────────────────────────────────

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

// ── Auth ────────────────────────────────────────────────────────────

export interface ApiLoginRequest {
  email: string;
  password: string;
}

export interface ApiLoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: ApiUserProfile;
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

// ── Generic Error ───────────────────────────────────────────────────

export interface ApiError {
  detail: string;
  status_code: number;
  error_type?: string;
}

// ── Memory ──────────────────────────────────────────────────────────

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

export interface ApiMemoryCreate {
  type: ApiMemoryEntry["type"];
  content: string;
  source?: string;
  tags?: string[];
  pinned?: boolean;
}

export interface ApiMemoryUpdate {
  content?: string;
  tags?: string[];
  pinned?: boolean;
  relevance?: number;
}

export interface ApiMemoryListResponse {
  items: ApiMemoryEntry[];
  total: number;
}

// ── Filesystem / Sandbox ────────────────────────────────────────────

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

export interface ApiFsFileContent {
  path: string;
  content: string;
  mime: string;
  size: number;
}
