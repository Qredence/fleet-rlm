export { rlmApiConfig, isRlmCoreEnabled, isRlmWsEnabled } from "./config";
export {
  SUPPORTED_SECTIONS,
  UNSUPPORTED_SECTION_REASON,
  isSectionSupported,
} from "./capabilities";
export {
  BACKEND_CAPABILITY_TOOLTIP,
  BACKEND_CAPABILITY_TOAST,
  BACKEND_CAPABILITY_BANNER_TITLE,
} from "./messages";
export { rlmApiClient, RlmApiError } from "./client";
export { rlmCoreEndpoints } from "./endpoints";
export { streamChatOverWs, createBackendSessionId } from "./wsClient";
export type {
  WsTraceMode,
  WsConnectionStatus,
  WsConnectionOptions,
  WsMessageRequest,
  WsCancelRequest,
  WsClientMessage,
  WsEventKind,
  WsEventPayload,
  WsServerEvent,
  WsServerError,
  WsServerMessage,
} from "./wsClient";
export type {
  OpenApiPaths,
  HealthResponse,
  ReadyResponse,
  ChatRequest,
  ChatResponse,
  TaskRequest,
  TaskResponse,
  SessionStateResponse,
  SessionStateSummary,
  RlmTaskType,
} from "./types";
