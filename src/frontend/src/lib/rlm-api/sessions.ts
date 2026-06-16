import { rlmApiClient } from "@/lib/rlm-api/client";
import { withQuery } from "@/lib/rlm-api/query";
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

const BASE = "/api/v1/sessions";
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

function sessionPath(sessionId: string, suffix = ""): string {
  return `${BASE}/${encodeURIComponent(sessionId)}${suffix}`;
}

export const sessionsEndpoints = {
  list(input: ListSessionsInput = {}, signal?: AbortSignal) {
    return rlmApiClient.get<SessionListResponse>(
      withQuery(BASE, {
        search: input.search,
        status: input.status,
        limit: input.limit,
        offset: input.offset,
      }),
      signal,
    );
  },

  get(sessionId: string, signal?: AbortSignal) {
    return rlmApiClient.get<SessionDetailResponse>(sessionPath(sessionId), signal);
  },

  patch(sessionId: string, body: SessionPatchRequest, signal?: AbortSignal) {
    return rlmApiClient.patch<SessionDetailResponse>(sessionPath(sessionId), body, signal);
  },

  delete(sessionId: string, signal?: AbortSignal) {
    return rlmApiClient.delete<SessionDeleteResponse>(sessionPath(sessionId), signal);
  },

  restore(sessionId: string, signal?: AbortSignal) {
    return rlmApiClient.post<SessionRestoreResponse>(
      sessionPath(sessionId, "/restore"),
      undefined,
      signal,
    );
  },

  turns(sessionId: string, input: ListTurnsInput = {}, signal?: AbortSignal) {
    return rlmApiClient.get<TurnListResponse>(
      withQuery(sessionPath(sessionId, "/turns"), {
        limit: input.limit,
        offset: input.offset,
      }),
      signal,
    );
  },

  stats(sessionId: string, signal?: AbortSignal) {
    return rlmApiClient.get<SessionStatsResponse>(sessionPath(sessionId, "/stats"), signal);
  },

  traces(sessionId: string, input: ListSessionTracesInput = {}, signal?: AbortSignal) {
    return rlmApiClient.get<SessionTraceListResponse>(
      withQuery(sessionPath(sessionId, "/traces"), {
        limit: input.limit,
        offset: input.offset,
      }),
      signal,
    );
  },

  traceDebug(sessionId: string, input: SessionTraceDebugInput = {}, signal?: AbortSignal) {
    return rlmApiClient.get<SessionTraceDebugResponse>(
      withQuery(sessionPath(sessionId, "/trace-debug"), {
        trace_id: input.traceId,
        client_request_id: input.traceId ? undefined : input.clientRequestId,
      }),
      signal,
    );
  },

  exportTraces(
    sessionId: string,
    body: SessionTraceExportRequest = { format: "both" },
    signal?: AbortSignal,
  ) {
    return rlmApiClient.post<SessionTraceExportResponse>(
      sessionPath(sessionId, "/trace-export"),
      body,
      signal,
      SESSION_TRACE_EXPORT_TIMEOUT_MS,
    );
  },

  exportDataset(sessionId: string, body: SessionExportRequest, signal?: AbortSignal) {
    return rlmApiClient.post<DatasetResponse>(sessionPath(sessionId, "/export"), body, signal);
  },
};
