import { typedClient, unwrap, withTimeout } from "@/lib/rlm-api/typed-client";
import type { components } from "@/lib/rlm-api/generated/openapi";

export type SessionListItem = components["schemas"]["SessionListItem"];
export type SessionListResponse = components["schemas"]["SessionListResponse"];
export type SessionDetailResponse = components["schemas"]["SessionDetailResponse"];
export type SessionPatchRequest = components["schemas"]["SessionPatchRequest"];
export type SessionDeleteResponse = components["schemas"]["SessionDeleteResponse"];
export type SessionRestoreResponse = components["schemas"]["SessionRestoreResponse"];
export type SessionStatsResponse = components["schemas"]["SessionStatsResponse"];
export type TurnItem = components["schemas"]["TurnItem"];
export type TurnListResponse = components["schemas"]["TurnListResponse"];
export type SessionTraceItem = components["schemas"]["SessionTraceItem"];
export type SessionTraceListResponse = components["schemas"]["SessionTraceListResponse"];
export type SessionTraceDebugSpan = components["schemas"]["SessionTraceDebugSpan"];
export type SessionTraceDebugResponse = components["schemas"]["SessionTraceDebugResponse"];
export type SessionTraceExportRequest = components["schemas"]["SessionTraceExportRequest"];
export type SessionTraceExportResponse = components["schemas"]["SessionTraceExportResponse"];
export type SessionExportRequest = components["schemas"]["SessionExportRequest"];
export type DatasetResponse = components["schemas"]["DatasetResponse"];

const SESSION_TRACE_EXPORT_TIMEOUT_MS = 120_000;

export interface ListSessionsInput {
  search?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export interface ListTurnsInput {
  limit?: number;
  offset?: number;
}

export interface ListSessionTracesInput {
  limit?: number;
  offset?: number;
}

export interface SessionTraceDebugInput {
  traceId?: string | null;
  clientRequestId?: string | null;
}

export const sessionsEndpoints = {
  list(input: ListSessionsInput = {}, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/sessions", {
        params: {
          query: {
            search: input.search,
            status: input.status,
            limit: input.limit,
            offset: input.offset,
          },
        },
        signal: withTimeout(signal),
      }),
    );
  },

  get(sessionId: string, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/sessions/{session_id}", {
        params: { path: { session_id: sessionId } },
        signal: withTimeout(signal),
      }),
    );
  },

  patch(sessionId: string, body: SessionPatchRequest, signal?: AbortSignal) {
    return unwrap(
      typedClient.PATCH("/api/v1/sessions/{session_id}", {
        params: { path: { session_id: sessionId } },
        body,
        signal: withTimeout(signal),
      }),
    );
  },

  delete(sessionId: string, signal?: AbortSignal) {
    return unwrap(
      typedClient.DELETE("/api/v1/sessions/{session_id}", {
        params: { path: { session_id: sessionId } },
        signal: withTimeout(signal),
      }),
    );
  },

  restore(sessionId: string, signal?: AbortSignal) {
    return unwrap(
      typedClient.POST("/api/v1/sessions/{session_id}/restore", {
        params: { path: { session_id: sessionId } },
        signal: withTimeout(signal),
      }),
    );
  },

  turns(sessionId: string, input: ListTurnsInput = {}, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/sessions/{session_id}/turns", {
        params: {
          path: { session_id: sessionId },
          query: { limit: input.limit, offset: input.offset },
        },
        signal: withTimeout(signal),
      }),
    );
  },

  stats(sessionId: string, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/sessions/{session_id}/stats", {
        params: { path: { session_id: sessionId } },
        signal: withTimeout(signal),
      }),
    );
  },

  traces(sessionId: string, input: ListSessionTracesInput = {}, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/sessions/{session_id}/traces", {
        params: {
          path: { session_id: sessionId },
          query: { limit: input.limit, offset: input.offset },
        },
        signal: withTimeout(signal),
      }),
    );
  },

  traceDebug(sessionId: string, input: SessionTraceDebugInput = {}, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/sessions/{session_id}/trace-debug", {
        params: {
          path: { session_id: sessionId },
          query: {
            trace_id: input.traceId ?? undefined,
            client_request_id: input.traceId ? undefined : (input.clientRequestId ?? undefined),
          },
        },
        signal: withTimeout(signal),
      }),
    );
  },

  exportTraces(
    sessionId: string,
    body: SessionTraceExportRequest = { format: "both" },
    signal?: AbortSignal,
  ) {
    return unwrap(
      typedClient.POST("/api/v1/sessions/{session_id}/trace-export", {
        params: { path: { session_id: sessionId } },
        body,
        signal: withTimeout(signal, SESSION_TRACE_EXPORT_TIMEOUT_MS),
      }),
    );
  },

  exportDataset(sessionId: string, body: SessionExportRequest, signal?: AbortSignal) {
    return unwrap(
      typedClient.POST("/api/v1/sessions/{session_id}/export", {
        params: { path: { session_id: sessionId } },
        body,
        signal: withTimeout(signal),
      }),
    );
  },
};
