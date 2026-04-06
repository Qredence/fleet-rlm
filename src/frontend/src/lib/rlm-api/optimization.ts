import { rlmApiClient } from "@/lib/rlm-api/client";

export interface GEPAStatusResponse {
  available: boolean;
  mlflow_enabled: boolean;
  gepa_installed: boolean;
  guidance: string[];
}

export interface GEPAOptimizationRequest {
  dataset_path: string;
  program_spec: string;
  output_path?: string | null;
  auto: "light" | "medium" | "heavy";
  train_ratio: number;
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
}

export const optimizationEndpoints = {
  status(signal?: AbortSignal) {
    return rlmApiClient.get<GEPAStatusResponse>("/api/v1/optimization/status", signal);
  },

  run(input: GEPAOptimizationRequest, signal?: AbortSignal) {
    return rlmApiClient.post<GEPAOptimizationResponse>("/api/v1/optimization/run", input, signal);
  },
};
