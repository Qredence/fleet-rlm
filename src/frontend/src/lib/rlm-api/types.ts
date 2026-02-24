import type {
  components,
  operations,
  paths,
} from "@/lib/rlm-api/generated/openapi";

export type OpenApiPaths = paths;

export type HealthResponse = components["schemas"]["HealthResponse"];
export type ReadyResponse = components["schemas"]["ReadyResponse"];
export type ChatRequest = components["schemas"]["ChatRequest"];
export type SessionStateResponse =
  components["schemas"]["SessionStateResponse"];
export type SessionStateSummary = components["schemas"]["SessionStateSummary"];

// `/api/v1/chat` currently returns an untyped object in the backend OpenAPI schema.
export type ChatResponse =
  operations["chat_api_v1_chat_post"]["responses"][200]["content"]["application/json"];

// Legacy runner task endpoints remain consumed by frontend helpers.
export type RlmTaskType =
  | "basic"
  | "architecture"
  | "api_endpoints"
  | "error_patterns"
  | "long_context"
  | "summarize"
  | "custom_tool";

export type TaskRequest = {
  task_type: RlmTaskType;
  question?: string;
  docs_path?: string | null;
  query?: string;
  max_iterations?: number;
  max_llm_calls?: number;
  timeout?: number;
  chars?: number;
  verbose?: boolean;
};

export type TaskResponse = {
  ok?: boolean;
  result?: Record<string, unknown>;
  error?: string | null;
};

// Runtime settings/diagnostics endpoints are newer than the current generated
// frontend OpenAPI snapshot, so keep these local types in sync with backend
// `server/schemas/core.py` until the snapshot is regenerated.
export type RuntimeSettingsSnapshot = {
  env_path: string;
  keys: string[];
  values: Record<string, string>;
  masked_values: Record<string, string>;
};

export type RuntimeSettingsUpdateResponse = {
  updated: string[];
  env_path: string;
};

export type RuntimeConnectivityTestKind = "modal" | "lm";

export type RuntimeConnectivityTestResponse = {
  kind: RuntimeConnectivityTestKind;
  ok: boolean;
  preflight_ok: boolean;
  checked_at: string;
  checks: Record<string, unknown>;
  guidance: string[];
  latency_ms?: number | null;
  output_preview?: string | null;
  error?: string | null;
};

export type RuntimeTestCache = {
  modal: RuntimeConnectivityTestResponse | null;
  lm: RuntimeConnectivityTestResponse | null;
};

export type RuntimeStatusResponse = {
  app_env: string;
  write_enabled: boolean;
  ready: boolean;
  llm: Record<string, unknown>;
  modal: Record<string, unknown>;
  tests: RuntimeTestCache;
  guidance: string[];
};
