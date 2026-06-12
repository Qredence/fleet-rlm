import { rlmApiClient } from "@/lib/rlm-api/client";
import type { components } from "@/lib/rlm-api/generated/openapi";

export type GEPAStatusResponse = components["schemas"]["GEPAStatusResponse"];
export type GEPAModuleInfo = components["schemas"]["GEPAModuleInfo"];
export type GEPAOptimizationRequest = components["schemas"]["GEPAOptimizationRequest"];
export type DatasetResponse = components["schemas"]["DatasetResponse"];
export type DatasetListResponse = components["schemas"]["DatasetListResponse"];
export type SessionTraceExportRequest = components["schemas"]["SessionTraceExportRequest"];
export type SessionTraceExportResponse = components["schemas"]["SessionTraceExportResponse"];
export type OptimizationRunCreatedResponse =
  components["schemas"]["OptimizationRunCreatedResponse"];
export type OptimizationRunResponse = components["schemas"]["OptimizationRunResponse"];
export type OptimizationRunDetailResponse = components["schemas"]["OptimizationRunDetailResponse"];
export type OptimizationPromotionDraftResponse =
  components["schemas"]["OptimizationPromotionDraftResponse"];

export interface UploadOptimizationDatasetInput {
  file: File;
  moduleSlug?: string | null;
}

export interface ListOptimizationRunsInput {
  status?: string;
  limit?: number;
  offset?: number;
}

export interface ListOptimizationDatasetsInput {
  moduleSlug?: string | null;
  limit?: number;
  offset?: number;
}

function withQuery(path: string, params: Record<string, string | number | null | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export const optimizationEndpoints = {
  status(signal?: AbortSignal) {
    return rlmApiClient.get<GEPAStatusResponse>("/api/v1/optimization/status", signal);
  },

  modules(signal?: AbortSignal) {
    return rlmApiClient.get<GEPAModuleInfo[]>("/api/v1/optimization/modules", signal);
  },

  datasets(input: ListOptimizationDatasetsInput = {}, signal?: AbortSignal) {
    return rlmApiClient.get<DatasetListResponse>(
      withQuery("/api/v1/optimization/datasets", {
        module_slug: input.moduleSlug,
        limit: input.limit,
        offset: input.offset,
      }),
      signal,
    );
  },

  uploadDataset(input: UploadOptimizationDatasetInput, signal?: AbortSignal) {
    const formData = new FormData();
    formData.append("file", input.file);
    if (input.moduleSlug) {
      formData.append("module_slug", input.moduleSlug);
    }
    return rlmApiClient.postForm<DatasetResponse>(
      "/api/v1/optimization/datasets",
      formData,
      signal,
    );
  },

  createRun(body: GEPAOptimizationRequest, signal?: AbortSignal) {
    return rlmApiClient.post<OptimizationRunCreatedResponse>(
      "/api/v1/optimization/runs",
      body,
      signal,
      120_000,
    );
  },

  runs(input: ListOptimizationRunsInput = {}, signal?: AbortSignal) {
    return rlmApiClient.get<OptimizationRunResponse[]>(
      withQuery("/api/v1/optimization/runs", {
        status: input.status,
        limit: input.limit,
        offset: input.offset,
      }),
      signal,
    );
  },

  run(runId: string, signal?: AbortSignal) {
    return rlmApiClient.get<OptimizationRunResponse>(
      `/api/v1/optimization/runs/${encodeURIComponent(runId)}`,
      signal,
    );
  },

  runDetails(runId: string, signal?: AbortSignal) {
    return rlmApiClient.get<OptimizationRunDetailResponse>(
      `/api/v1/optimization/runs/${encodeURIComponent(runId)}/details`,
      signal,
    );
  },

  createPromotionDraft(runId: string, signal?: AbortSignal) {
    return rlmApiClient.post<OptimizationPromotionDraftResponse>(
      `/api/v1/optimization/runs/${encodeURIComponent(runId)}/promotion-drafts`,
      {},
      signal,
      120_000,
    );
  },

  exportSessionTraces(
    sessionId: string,
    body: SessionTraceExportRequest = { format: "both" },
    signal?: AbortSignal,
  ) {
    return rlmApiClient.post<SessionTraceExportResponse>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/trace-export`,
      body,
      signal,
      120_000,
    );
  },
};
