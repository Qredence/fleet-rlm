import type { components } from "./generated/openapi";
import { rlmApiClient } from "./client";

// ---------------------------------------------------------------------------
// Types derived from generated OpenAPI schema
// ---------------------------------------------------------------------------

export type SessionListItem = components["schemas"]["SessionListItem"];
export type SessionListResponse = components["schemas"]["SessionListResponse"];
export type SessionDetailResponse = components["schemas"]["SessionDetailResponse"];
export type TurnItem = components["schemas"]["TurnItem"];
export type TurnListResponse = components["schemas"]["TurnListResponse"];
export type SessionExportRequest = components["schemas"]["SessionExportRequest"];
export type DatasetResponse = components["schemas"]["DatasetResponse"];

export interface SessionListParams {
  search?: string | null;
  status?: string | null;
  limit?: number;
  offset?: number;
}

export interface TurnListParams {
  limit?: number;
  offset?: number;
}

// ---------------------------------------------------------------------------
// Query key factory (TanStack Query compatible)
// ---------------------------------------------------------------------------

export const sessionKeys = {
  all: ["sessions"] as const,
  lists: () => [...sessionKeys.all, "list"] as const,
  list: (params: SessionListParams) => [...sessionKeys.lists(), params] as const,
  details: () => [...sessionKeys.all, "detail"] as const,
  detail: (id: string) => [...sessionKeys.details(), id] as const,
  turns: (id: string, params?: TurnListParams) =>
    [...sessionKeys.detail(id), "turns", params ?? {}] as const,
};

// ---------------------------------------------------------------------------
// Endpoint functions
// ---------------------------------------------------------------------------

export const sessionEndpoints = {
  listSessions(params?: SessionListParams, signal?: AbortSignal) {
    const searchParams = new URLSearchParams();
    if (params?.search) searchParams.set("search", params.search);
    if (params?.status) searchParams.set("status", params.status);
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    const qs = searchParams.toString();
    return rlmApiClient.get<SessionListResponse>(`/api/v1/sessions${qs ? `?${qs}` : ""}`, signal);
  },

  getSession(id: string, signal?: AbortSignal) {
    return rlmApiClient.get<SessionDetailResponse>(`/api/v1/sessions/${id}`, signal);
  },

  getSessionTurns(id: string, params?: TurnListParams, signal?: AbortSignal) {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    const qs = searchParams.toString();
    return rlmApiClient.get<TurnListResponse>(
      `/api/v1/sessions/${id}/turns${qs ? `?${qs}` : ""}`,
      signal,
    );
  },

  deleteSession(id: string, signal?: AbortSignal) {
    return rlmApiClient.delete<void>(`/api/v1/sessions/${id}`, signal);
  },

  exportSession(id: string, moduleSlug: string, signal?: AbortSignal) {
    return rlmApiClient.post<DatasetResponse>(
      `/api/v1/sessions/${id}/export`,
      { module_slug: moduleSlug },
      signal,
    );
  },
};
