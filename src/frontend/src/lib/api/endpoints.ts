/**
 * Typed API endpoint helpers for currently supported `lib/api` routes.
 *
 * Deprecated/planned route families were removed from the backend and are not
 * represented here anymore.
 */

import { apiClient } from "@/lib/api/client";

const API_PREFIX = "/api/v1";

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

export interface MemoryListParams {
  type?: string;
  search?: string;
  pinned?: boolean;
  sortBy?: "relevance" | "createdAt" | "updatedAt";
  sortOrder?: "asc" | "desc";
}

export interface SessionStateSummary {
  key: string;
  workspace_id: string;
  user_id: string;
  session_id: string | null;
  history_turns: number;
  document_count: number;
  memory_count: number;
  log_count: number;
  artifact_count: number;
  updated_at: string | null;
}

export interface SessionStateResponse {
  ok: boolean;
  sessions: SessionStateSummary[];
}

export const sessionStateEndpoints = {
  /** GET /api/v1/sessions/state — List active/restored session summaries */
  list(signal?: AbortSignal) {
    return apiClient.get<SessionStateResponse>(
      `${API_PREFIX}/sessions/state`,
      undefined,
      signal,
    );
  },
};
