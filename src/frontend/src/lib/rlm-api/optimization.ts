import { sessionsEndpoints } from "@/lib/rlm-api/sessions";
import { typedClient, unwrap, withTimeout } from "@/lib/rlm-api/typed-client";
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

export const optimizationEndpoints = {
  status(signal?: AbortSignal) {
    return unwrap(typedClient.GET("/api/v1/optimization/status", { signal: withTimeout(signal) }));
  },

  modules(signal?: AbortSignal) {
    return unwrap(typedClient.GET("/api/v1/optimization/modules", { signal: withTimeout(signal) }));
  },

  datasets(input: ListOptimizationDatasetsInput = {}, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/optimization/datasets", {
        params: {
          query: { module_slug: input.moduleSlug, limit: input.limit, offset: input.offset },
        },
        signal: withTimeout(signal),
      }),
    );
  },

  uploadDataset(input: UploadOptimizationDatasetInput, signal?: AbortSignal) {
    const formData = new FormData();
    formData.append("file", input.file);
    if (input.moduleSlug) {
      formData.append("module_slug", input.moduleSlug);
    }
    return unwrap(
      typedClient.POST("/api/v1/optimization/datasets", {
        body: formData as never,
        signal: withTimeout(signal),
        bodySerializer: () => formData,
      }),
    );
  },

  createRun(body: GEPAOptimizationRequest, signal?: AbortSignal) {
    return unwrap(
      typedClient.POST("/api/v1/optimization/runs", {
        body,
        signal: withTimeout(signal, 120_000),
      }),
    );
  },

  runs(input: ListOptimizationRunsInput = {}, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/optimization/runs", {
        params: { query: { status: input.status, limit: input.limit, offset: input.offset } },
        signal: withTimeout(signal),
      }),
    );
  },

  run(runId: string, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/optimization/runs/{run_id}", {
        params: { path: { run_id: runId } },
        signal: withTimeout(signal),
      }),
    );
  },

  runDetails(runId: string, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/optimization/runs/{run_id}/details", {
        params: { path: { run_id: runId } },
        signal: withTimeout(signal),
      }),
    );
  },

  createPromotionDraft(runId: string, signal?: AbortSignal) {
    return unwrap(
      typedClient.POST("/api/v1/optimization/runs/{run_id}/promotion-drafts", {
        params: { path: { run_id: runId } },
        signal: withTimeout(signal, 120_000),
      }),
    );
  },

  /** @deprecated Prefer `sessionsEndpoints.exportTraces`; retained for callers. */
  exportSessionTraces(
    sessionId: string,
    body: SessionTraceExportRequest = { format: "both" },
    signal?: AbortSignal,
  ) {
    return sessionsEndpoints.exportTraces(sessionId, body, signal);
  },
};
