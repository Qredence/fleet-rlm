export { rlmApiConfig, isRlmCoreEnabled, isRlmWsEnabled } from "@/lib/rlm-api/config";
export {
  SUPPORTED_SECTIONS,
  UNSUPPORTED_SECTION_REASON,
  isSectionSupported,
} from "@/lib/rlm-api/capabilities";
export { rlmApiClient, RlmApiError } from "@/lib/rlm-api/client";
export {
  streamChatOverWs,
  sendCommandOverWs,
  subscribeToExecutionStream,
  createBackendSessionId,
} from "@/lib/rlm-api/wsClient";
export { authEndpoints } from "@/lib/rlm-api/auth";
export type {
  WsTraceMode,
  WsRuntimeMode,
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
