import { rlmApiClient } from "@/lib/rlm-api/client";

export interface GEPAStatusResponse {
  available: boolean;
  mlflow_enabled: boolean;
  gepa_installed: boolean;
  guidance: string[];
}

export interface GEPAModuleInfo {
  slug: string;
  label: string;
  program_spec: string;
  required_dataset_keys: string[];
}

export interface GEPAOptimizationRequest {
  dataset_path: string;
  program_spec: string;
  output_path?: string | null;
  auto: "light" | "medium" | "heavy";
  train_ratio: number;
  module_slug?: string | null;
}

export interface GEPAOptimizationResponse {
  ok: boolean;
  optimizer: string;
  program_spec: string;
  train_examples: number;
  validation_examples: number;
  validation_score: number | null;
  output_path: string | null;
  error: string | null;
  manifest_path?: string | null;
  module_slug?: string | null;
}

export interface OptimizationRunCreated {
  run_id: number;
  status: string;
}

export interface OptimizationRunSummary {
  id: number;
  status: "running" | "completed" | "failed";
  module_slug: string | null;
  program_spec: string;
  optimizer: string;
  auto: string | null;
  train_ratio: number;
  dataset_path: string | null;
  train_examples: number | null;
  validation_examples: number | null;
  validation_score: number | null;
  output_path: string | null;
  manifest_path: string | null;
  error: string | null;
  phase: string | null;
  started_at: string;
  completed_at: string | null;
}

export const optimizationEndpoints = {
  status(signal?: AbortSignal) {
    return rlmApiClient.get<GEPAStatusResponse>("/api/v1/optimization/status", signal);
  },

  modules(signal?: AbortSignal) {
    return rlmApiClient.get<GEPAModuleInfo[]>("/api/v1/optimization/modules", signal);
  },

  run(input: GEPAOptimizationRequest, signal?: AbortSignal) {
    // GEPA optimization can take several minutes; use a 10-minute timeout
    return rlmApiClient.post<GEPAOptimizationResponse>(
      "/api/v1/optimization/run",
      input,
      signal,
      600_000,
    );
  },

  createRun(input: GEPAOptimizationRequest, signal?: AbortSignal) {
    return rlmApiClient.post<OptimizationRunCreated>(
      "/api/v1/optimization/runs",
      input,
      signal,
    );
  },

  listRuns(params?: { status?: string; limit?: number; offset?: number }, signal?: AbortSignal) {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set("status", params.status);
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    const qs = searchParams.toString();
    return rlmApiClient.get<OptimizationRunSummary[]>(
      `/api/v1/optimization/runs${qs ? `?${qs}` : ""}`,
      signal,
    );
  },

  getRun(runId: number, signal?: AbortSignal) {
    return rlmApiClient.get<OptimizationRunSummary>(
      `/api/v1/optimization/runs/${runId}`,
      signal,
    );
  },
};

export const optimizationKeys = {
  all: ["optimization"] as const,
  status: () => [...optimizationKeys.all, "status"] as const,
  modules: () => [...optimizationKeys.all, "modules"] as const,
  runs: () => [...optimizationKeys.all, "runs"] as const,
  runsList: (params?: { status?: string }) =>
    [...optimizationKeys.runs(), "list", params ?? {}] as const,
  runDetail: (id: number) => [...optimizationKeys.runs(), "detail", id] as const,
};
