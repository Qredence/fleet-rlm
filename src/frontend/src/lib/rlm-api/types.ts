import type {
  components,
  operations,
  paths,
} from "@/lib/rlm-api/generated/openapi";

export type OpenApiPaths = paths;

export type HealthResponse = components["schemas"]["HealthResponse"];
export type ReadyResponse = components["schemas"]["ReadyResponse"];
export type ChatRequest = components["schemas"]["ChatRequest"];
export type AuthLoginResponse = components["schemas"]["AuthLoginResponse"];
export type AuthLogoutResponse = components["schemas"]["AuthLogoutResponse"];
export type AuthMeResponse = components["schemas"]["AuthMeResponse"];
export type SessionStateResponse =
  components["schemas"]["SessionStateResponse"];
export type SessionStateSummary = components["schemas"]["SessionStateSummary"];

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

export type RuntimeSettingsSnapshot =
  components["schemas"]["RuntimeSettingsSnapshot"];
export type RuntimeSettingsUpdateResponse =
  components["schemas"]["RuntimeSettingsUpdateResponse"];
export type RuntimeConnectivityTestKind =
  components["schemas"]["RuntimeConnectivityTestResponse"]["kind"];
export type RuntimeConnectivityTestResponse =
  components["schemas"]["RuntimeConnectivityTestResponse"];
export type RuntimeTestCache = components["schemas"]["RuntimeTestCache"];
export type RuntimeStatusResponse = components["schemas"]["RuntimeStatusResponse"];
