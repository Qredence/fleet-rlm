export {
  rlmApiConfig,
  isRlmCoreEnabled,
  isRlmWsEnabled,
} from "@/lib/rlm-api/config";
export {
  SUPPORTED_SECTIONS,
  UNSUPPORTED_SECTION_REASON,
  isSectionSupported,
} from "@/lib/rlm-api/capabilities";
export {
  BACKEND_CAPABILITY_TOOLTIP,
  BACKEND_CAPABILITY_TOAST,
  BACKEND_CAPABILITY_BANNER_TITLE,
} from "@/lib/rlm-api/messages";
export { rlmApiClient, RlmApiError } from "@/lib/rlm-api/client";
export { authEndpoints } from "@/lib/rlm-api/auth";
export {
  streamChatOverWs,
  sendCommandOverWs,
  subscribeToExecutionStream,
  createBackendSessionId,
} from "@/lib/rlm-api/wsClient";
export type {
  WsTraceMode,
  WsConnectionStatus,
  WsConnectionOptions,
  WsMessageRequest,
  WsCommandRequest,
  WsCancelRequest,
  WsClientMessage,
  WsEventKind,
  WsEventPayload,
  WsServerEvent,
  WsServerError,
  WsServerMessage,
} from "@/lib/rlm-api/wsClient";
export type {
  OpenApiPaths,
  HealthResponse,
  ReadyResponse,
  AuthLoginResponse,
  AuthLogoutResponse,
  AuthMeResponse,
  SessionStateResponse,
  SessionStateSummary,
  RuntimeConnectivityTestKind,
  RuntimeConnectivityTestResponse,
  RuntimeSettingsSnapshot,
  RuntimeSettingsUpdateResponse,
  RuntimeStatusResponse,
  RuntimeTestCache,
} from "@/lib/rlm-api/types";
