import type { components, operations, paths } from "./generated/openapi";

export type OpenApiPaths = paths;

export type HealthResponse = components["schemas"]["HealthResponse"];
export type ReadyResponse = components["schemas"]["ReadyResponse"];
export type ChatRequest = components["schemas"]["ChatRequest"];
export type TaskRequest = components["schemas"]["TaskRequest"];
export type TaskResponse = components["schemas"]["TaskResponse"];
export type SessionStateResponse =
  components["schemas"]["SessionStateResponse"];
export type SessionStateSummary = components["schemas"]["SessionStateSummary"];

// `/chat` currently returns an untyped object in the backend OpenAPI schema.
export type ChatResponse =
  operations["chat_chat_post"]["responses"][200]["content"]["application/json"];

export type RlmTaskType = TaskRequest["task_type"];
