import { rlmApiClient } from "@/lib/rlm-api/client";
import type { components } from "@/lib/rlm-api/generated/openapi";

// ── Generated-type aliases ──────────────────────────────────────────
export type DatasetResponse = components["schemas"]["DatasetResponse"];
export type DatasetListResponse = components["schemas"]["DatasetListResponse"];
export type DatasetDetailResponse = components["schemas"]["DatasetDetailResponse"];
export type EvaluationResultItem = components["schemas"]["EvaluationResultItem"];
export type EvaluationResultsResponse = components["schemas"]["EvaluationResultsResponse"];
export type RunComparisonItem = components["schemas"]["RunComparisonItem"];
export type RunComparisonResponse = components["schemas"]["RunComparisonResponse"];
export type PromptSnapshotItem = components["schemas"]["PromptSnapshotItem"];

export interface GEPAStatusResponse {
  available: boolean;
  mlflow_enabled: boolean;
  gepa_installed: boolean;
  guidance: string[];
}

export interface GEPAModuleInfo {
  slug: string;
  label: string;
  description?: string;
  program_spec: string;
  required_dataset_keys: string[];
}

export interface GEPAOptimizationRequest {
  dataset_path?: string | null;
  dataset_id?: number | null;
  program_spec: string;
  output_path?: string | null;
  auto: "light" | "medium" | "heavy";
  train_ratio: number;
  module_slug?: string | null;
}

export interface TranscriptTurnInput {
  user_message?: string | null;
  assistant_message?: string | null;
}

export interface TranscriptDatasetRequest {
  module_slug: string;
  title?: string | null;
  turns: TranscriptTurnInput[];
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

// ── Existing optimization endpoints ─────────────────────────────────

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
    return rlmApiClient.post<OptimizationRunCreated>("/api/v1/optimization/runs", input, signal);
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
    return rlmApiClient.get<OptimizationRunSummary>(`/api/v1/optimization/runs/${runId}`, signal);
  },
};

// ── Dataset endpoints ───────────────────────────────────────────────

export const datasetEndpoints = {
  /** Upload a dataset file (.json/.jsonl) with optional module association. */
  async upload(
    file: File,
    moduleSlug?: string | null,
    signal?: AbortSignal,
  ): Promise<DatasetResponse> {
    const formData = new FormData();
    formData.append("file", file);
    if (moduleSlug) {
      formData.append("module_slug", moduleSlug);
    }

    return rlmApiClient.postForm<DatasetResponse>(
      "/api/v1/optimization/datasets",
      formData,
      signal,
    );
  },

  /** List registered datasets with optional module filter. */
  list(params?: { module_slug?: string; limit?: number; offset?: number }, signal?: AbortSignal) {
    const searchParams = new URLSearchParams();
    if (params?.module_slug) searchParams.set("module_slug", params.module_slug);
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    const qs = searchParams.toString();
    return rlmApiClient.get<DatasetListResponse>(
      `/api/v1/optimization/datasets${qs ? `?${qs}` : ""}`,
      signal,
    );
  },

  /** Get dataset detail with sample rows. */
  get(datasetId: number, signal?: AbortSignal) {
    return rlmApiClient.get<DatasetDetailResponse>(
      `/api/v1/optimization/datasets/${datasetId}`,
      signal,
    );
  },

  /** Create a dataset from transcript turns. */
  createFromTranscript(input: TranscriptDatasetRequest, signal?: AbortSignal) {
    return rlmApiClient.post<DatasetResponse>(
      "/api/v1/optimization/transcript-datasets",
      input,
      signal,
    );
  },
};

// ── Evaluation result endpoints ─────────────────────────────────────

export const evaluationEndpoints = {
  /** Get paginated per-example evaluation results for a run. */
  getResults(runId: number, params?: { limit?: number; offset?: number }, signal?: AbortSignal) {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    const qs = searchParams.toString();
    return rlmApiClient.get<EvaluationResultsResponse>(
      `/api/v1/optimization/runs/${runId}/results${qs ? `?${qs}` : ""}`,
      signal,
    );
  },
};

// ── Run comparison endpoints ────────────────────────────────────────

export const comparisonEndpoints = {
  /** Compare prompt diffs and scores across optimization runs. */
  compare(runIds: number[], signal?: AbortSignal) {
    const qs = runIds.join(",");
    return rlmApiClient.get<RunComparisonResponse>(
      `/api/v1/optimization/runs/compare?run_ids=${qs}`,
      signal,
    );
  },
};

// ── Query key factories ─────────────────────────────────────────────

export const optimizationKeys = {
  all: ["optimization"] as const,
  status: () => [...optimizationKeys.all, "status"] as const,
  modules: () => [...optimizationKeys.all, "modules"] as const,
  runs: () => [...optimizationKeys.all, "runs"] as const,
  runsList: (params?: { status?: string }) =>
    [...optimizationKeys.runs(), "list", params ?? {}] as const,
  runDetail: (id: number) => [...optimizationKeys.runs(), "detail", id] as const,
  runResults: (runId: number, params?: { limit?: number; offset?: number }) =>
    [...optimizationKeys.runs(), "results", runId, params ?? {}] as const,
  runComparison: (runIds: number[]) => [...optimizationKeys.runs(), "compare", ...runIds] as const,
  datasets: () => [...optimizationKeys.all, "datasets"] as const,
  datasetList: (params?: { module_slug?: string }) =>
    [...optimizationKeys.datasets(), "list", params ?? {}] as const,
  datasetDetail: (id: number) => [...optimizationKeys.datasets(), "detail", id] as const,
};
