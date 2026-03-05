import type { components, paths } from "@/lib/rlm-api/generated/openapi";

export type OpenApiPaths = paths;

export type HealthResponse = components["schemas"]["HealthResponse"];
export type ReadyResponse = components["schemas"]["ReadyResponse"];
export type AuthLoginResponse = components["schemas"]["AuthLoginResponse"];
export type AuthLogoutResponse = components["schemas"]["AuthLogoutResponse"];
export type AuthMeResponse = components["schemas"]["AuthMeResponse"];
export type SessionStateResponse =
  components["schemas"]["SessionStateResponse"];
export type SessionStateSummary = components["schemas"]["SessionStateSummary"];

export type RuntimeSettingsSnapshot =
  components["schemas"]["RuntimeSettingsSnapshot"];
export type RuntimeSettingsUpdateResponse =
  components["schemas"]["RuntimeSettingsUpdateResponse"];
export type RuntimeConnectivityTestKind =
  components["schemas"]["RuntimeConnectivityTestResponse"]["kind"];
export type RuntimeConnectivityTestResponse =
  components["schemas"]["RuntimeConnectivityTestResponse"];
export type RuntimeTestCache = components["schemas"]["RuntimeTestCache"];
export type RuntimeStatusResponse =
  components["schemas"]["RuntimeStatusResponse"];
