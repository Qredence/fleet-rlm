import type { components } from "@/lib/rlm-api/generated/openapi";

export type AuthMeResponse = components["schemas"]["AuthMeResponse"];
export type WebSocketTicketResponse = components["schemas"]["WebSocketTicketResponse"];

export type RuntimeSettingsSnapshot = components["schemas"]["RuntimeSettingsSnapshot"];
export type RuntimeSettingsUpdateResponse = components["schemas"]["RuntimeSettingsUpdateResponse"];
export type RuntimeConnectivityTestResponse =
  components["schemas"]["RuntimeConnectivityTestResponse"];
export type RuntimeStatusResponse = components["schemas"]["RuntimeStatusResponse"];
export type ServiceInfoResponse = components["schemas"]["ServiceInfoResponse"];
