/**
 * Typed API endpoint functions for fleet-rlm.
 *
 * Each function encapsulates a single REST call and returns the raw
 * (camelCased) backend response. The hooks layer applies adapters
 * to convert these into frontend types.
 *
 * All paths are relative to `apiConfig.baseUrl` and are prefixed
 * with `/api/v1/` — adjust the `API_PREFIX` constant if the backend
 * uses a different versioning scheme.
 *
 * ────────────────────────────────────────────────────────────────────
 * TODO: Validate endpoint paths against the actual fleet-rlm router
 *       mounts once the OpenAPI spec is available.
 * ────────────────────────────────────────────────────────────────────
 */

import { apiClient } from "@/lib/api/client";

// Adjust if the backend uses a different prefix
const API_PREFIX = "/api/v1";

function raiseLegacyContractError(endpoint: string): never {
  throw new Error(
    `[legacy-api] ${endpoint} is deprecated and intentionally disabled. Use "@/lib/rlm-api" typed endpoints.`,
  );
}

// ── Tasks (→ Skills) ────────────────────────────────────────────────

export interface TaskListParams {
  page?: number;
  pageSize?: number;
  domain?: string;
  category?: string;
  status?: string;
  search?: string;
  sortBy?: string;
  sortOrder?: "asc" | "desc";
}

export const taskEndpoints = {
  /** GET /api/v1/tasks — List all tasks with optional filters */
  list(params?: TaskListParams, signal?: AbortSignal) {
    return apiClient.get<{
      items: Record<string, unknown>[];
      total: number;
      page: number;
      pageSize: number;
      hasMore: boolean;
    }>(
      `${API_PREFIX}/tasks`,
      params as Record<string, string | number | boolean | undefined>,
      signal,
    );
  },

  /** GET /api/v1/tasks/:id — Get a single task by ID */
  get(id: string, signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>>(
      `${API_PREFIX}/tasks/${id}`,
      undefined,
      signal,
    );
  },

  /** POST /api/v1/tasks — Create a new task */
  create(body: Record<string, unknown>) {
    return apiClient.post<Record<string, unknown>>(`${API_PREFIX}/tasks`, body);
  },

  /** PUT /api/v1/tasks/:id — Update an existing task */
  update(id: string, body: Record<string, unknown>) {
    return apiClient.put<Record<string, unknown>>(
      `${API_PREFIX}/tasks/${id}`,
      body,
    );
  },

  /** DELETE /api/v1/tasks/:id — Delete a task */
  delete(id: string) {
    return apiClient.delete<void>(`${API_PREFIX}/tasks/${id}`);
  },

  /** GET /api/v1/tasks/:id/content — Get generated SKILL.md content */
  getContent(id: string, signal?: AbortSignal) {
    return apiClient.get<{ content: string }>(
      `${API_PREFIX}/tasks/${id}/content`,
      undefined,
      signal,
    );
  },
};

// ── Taxonomy ────────────────────────────────────────────────────────

export const taxonomyEndpoints = {
  /** GET /api/v1/taxonomy — Get the full taxonomy tree */
  getTree(signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>[]>(
      `${API_PREFIX}/taxonomy`,
      undefined,
      signal,
    );
  },

  /** GET /api/v1/taxonomy/:path — Get a specific subtree */
  getSubtree(path: string, signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>>(
      `${API_PREFIX}/taxonomy/${encodeURIComponent(path)}`,
      undefined,
      signal,
    );
  },
};

// ── Sessions ────────────────────────────────────────────────────────

export const sessionEndpoints = {
  /** GET /api/v1/sessions — List user sessions */
  list(signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>[]>(
      `${API_PREFIX}/sessions`,
      undefined,
      signal,
    );
  },

  /** POST /api/v1/sessions — Create a new session */
  create(body?: { title?: string; metadata?: Record<string, unknown> }) {
    return apiClient.post<Record<string, unknown>>(
      `${API_PREFIX}/sessions`,
      body,
    );
  },

  /** GET /api/v1/sessions/:id — Get session details with messages */
  get(id: string, signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>>(
      `${API_PREFIX}/sessions/${id}`,
      undefined,
      signal,
    );
  },

  /** DELETE /api/v1/sessions/:id — Archive/delete a session */
  delete(id: string) {
    return apiClient.delete<void>(`${API_PREFIX}/sessions/${id}`);
  },
};

// ── Chat ────────────────────────────────────────────────────────────

export const chatEndpoints = {
  /**
   * @deprecated Legacy chat REST helpers drift from the canonical backend chat contract.
   * Use `rlmCoreEndpoints.chat()` and WS helpers from `@/lib/rlm-api` instead.
   */
  /** POST /api/v1/chat — Send a chat message (non-streaming) */
  send(body: {
    sessionId: string;
    message: string;
    context?: Record<string, unknown>;
  }) {
    void body;
    return raiseLegacyContractError("chatEndpoints.send");
  },

  /**
   * @deprecated Legacy SSE streaming route is no longer canonical for Fleet-RLM web chat.
   * Use `streamChatOverWs` from `@/lib/rlm-api`.
   */
  /** POST /api/v1/chat/stream — Send a chat message with SSE streaming */
  stream(
    body: {
      sessionId: string;
      message: string;
      context?: Record<string, unknown>;
    },
    signal?: AbortSignal,
  ) {
    void body;
    void signal;
    return raiseLegacyContractError("chatEndpoints.stream");
  },

  /**
   * @deprecated Legacy HITL REST route is not part of current backend contract.
   * Use WS command dispatch via `sendCommandOverWs` from `@/lib/rlm-api`.
   */
  /** POST /api/v1/chat/hitl — Respond to an HITL prompt */
  resolveHitl(body: { sessionId: string; messageId: string; action: string }) {
    void body;
    return raiseLegacyContractError("chatEndpoints.resolveHitl");
  },

  /**
   * @deprecated Legacy clarification REST route is not part of current backend contract.
   * Use WS command/event flows via `@/lib/rlm-api`.
   */
  /** POST /api/v1/chat/clarification — Respond to a clarification */
  resolveClarification(body: {
    sessionId: string;
    messageId: string;
    answer: string;
  }) {
    void body;
    return raiseLegacyContractError("chatEndpoints.resolveClarification");
  },
};

// ── Analytics ───────────────────────────────────────────────────────

export const analyticsEndpoints = {
  /** GET /api/v1/analytics — Get dashboard analytics */
  getDashboard(signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>>(
      `${API_PREFIX}/analytics`,
      undefined,
      signal,
    );
  },

  /** GET /api/v1/analytics/skills/:id — Get per-skill analytics */
  getSkillAnalytics(id: string, signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>>(
      `${API_PREFIX}/analytics/skills/${id}`,
      undefined,
      signal,
    );
  },
};

// ── Auth ────────────────────────────────────────────────────────────

export const authEndpoints = {
  /**
   * @deprecated This legacy API layer expects stale auth payload contracts.
   * Use `authEndpoints` from `@/lib/rlm-api/auth` instead.
   */
  /** POST /api/v1/auth/login — Authenticate user */
  login(body: { email: string; password: string }) {
    void body;
    return raiseLegacyContractError("authEndpoints.login");
  },

  /**
   * @deprecated Use typed `@/lib/rlm-api/auth` endpoints.
   */
  /** POST /api/v1/auth/logout — Invalidate session */
  logout() {
    return raiseLegacyContractError("authEndpoints.logout");
  },

  /**
   * @deprecated Use typed `@/lib/rlm-api/auth` endpoints.
   */
  /** GET /api/v1/auth/me — Get current user profile */
  me(signal?: AbortSignal) {
    void signal;
    return raiseLegacyContractError("authEndpoints.me");
  },
};

// ── Search ──────────────────────────────────────────────────────────

export const searchEndpoints = {
  /** GET /api/v1/search — Global search across skills, taxonomy, etc. */
  search(query: string, signal?: AbortSignal) {
    return apiClient.get<{
      skills: Record<string, unknown>[];
      taxonomy: Record<string, unknown>[];
    }>(`${API_PREFIX}/search`, { q: query }, signal);
  },
};

// ── Memory ──────────────────────────────────────────────────────────

export interface MemoryListParams {
  type?: string;
  search?: string;
  pinned?: boolean;
  sortBy?: "relevance" | "createdAt" | "updatedAt";
  sortOrder?: "asc" | "desc";
}

export const memoryEndpoints = {
  /** GET /api/v1/memory — List all memory entries */
  list(params?: MemoryListParams, signal?: AbortSignal) {
    return apiClient.get<{
      items: Record<string, unknown>[];
      total: number;
    }>(
      `${API_PREFIX}/memory`,
      params as Record<string, string | number | boolean | undefined>,
      signal,
    );
  },

  /** GET /api/v1/memory/:id — Get a single memory entry */
  get(id: string, signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>>(
      `${API_PREFIX}/memory/${id}`,
      undefined,
      signal,
    );
  },

  /** POST /api/v1/memory — Create a new memory entry */
  create(body: Record<string, unknown>) {
    return apiClient.post<Record<string, unknown>>(
      `${API_PREFIX}/memory`,
      body,
    );
  },

  /** PUT /api/v1/memory/:id — Update an existing memory entry */
  update(id: string, body: Record<string, unknown>) {
    return apiClient.put<Record<string, unknown>>(
      `${API_PREFIX}/memory/${id}`,
      body,
    );
  },

  /** DELETE /api/v1/memory/:id — Delete a memory entry */
  delete(id: string) {
    return apiClient.delete<void>(`${API_PREFIX}/memory/${id}`);
  },

  /** PUT /api/v1/memory/bulk-pin — Bulk pin/unpin entries */
  bulkPin(ids: string[], pinned: boolean) {
    return apiClient.put<Record<string, unknown>[]>(
      `${API_PREFIX}/memory/bulk-pin`,
      { ids, pinned },
    );
  },

  /** POST /api/v1/memory/bulk-delete — Bulk delete entries */
  bulkDelete(ids: string[]) {
    return apiClient.post<void>(`${API_PREFIX}/memory/bulk-delete`, { ids });
  },
};

// ── Filesystem / Sandbox ────────────────────────────────────────────

export const filesystemEndpoints = {
  /** GET /api/v1/sandbox — List sandbox volumes and directory tree */
  getTree(signal?: AbortSignal) {
    return apiClient.get<Record<string, unknown>[]>(
      `${API_PREFIX}/sandbox`,
      undefined,
      signal,
    );
  },

  /** GET /api/v1/sandbox/file?path=... — Get file content */
  getFileContent(path: string, signal?: AbortSignal) {
    return apiClient.get<{
      path: string;
      content: string;
      mime: string;
      size: number;
    }>(`${API_PREFIX}/sandbox/file`, { path }, signal);
  },
};
