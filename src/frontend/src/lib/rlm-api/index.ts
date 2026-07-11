export { rlmApiConfig, isRlmCoreEnabled, isRlmWsEnabled } from "@/lib/rlm-api/config";
export {
  SUPPORTED_SECTIONS,
  UNSUPPORTED_SECTION_REASON,
  isSectionSupported,
} from "@/lib/rlm-api/capabilities";
export { rlmApiClient, RlmApiError } from "@/lib/rlm-api/client";
export { typedClient, unwrap, withTimeout } from "@/lib/rlm-api/typed-client";
export {
  streamChatOverWs,
  sendCommandOverWs,
  subscribeToExecutionStream,
  createBackendSessionId,
} from "@/lib/rlm-api/ws-client";
export { authEndpoints } from "@/lib/rlm-api/auth";
export { evaluationsEndpoints } from "@/lib/rlm-api/evaluations";
export { infoEndpoints } from "@/lib/rlm-api/info";
export { optimizationEndpoints } from "@/lib/rlm-api/optimization";
export { sessionsEndpoints } from "@/lib/rlm-api/sessions";
export { volumesEndpoints } from "@/lib/rlm-api/volumes";
export type {
  SessionListItem,
  SessionListResponse,
  SessionDetailResponse,
  SessionStatsResponse,
  TurnItem,
  TurnListResponse,
  SessionTraceItem,
  SessionTraceListResponse,
  SessionTraceDebugSpan,
  SessionTraceDebugResponse,
} from "@/lib/rlm-api/sessions";
export type {
  EvaluationRequest,
  EvaluationRunResponse,
  EvaluationRunListItem,
  EvaluationRunListResponse,
  EvaluationReportResponse,
} from "@/lib/rlm-api/evaluations";
export type {
  DatasetListResponse,
  DatasetResponse,
  GEPAOptimizationRequest,
  GEPAModuleInfo,
  GEPAStatusResponse,
  SealedPromotionScorecard,
  OptimizationArtifactVersionResponse,
  OptimizationTargetActivationResponse,
  OptimizationRunCreatedResponse,
  OptimizationPromotionDraftResponse,
  OptimizationRunDetailResponse,
  OptimizationRunResponse,
  SessionTraceExportRequest,
  SessionTraceExportResponse,
  UploadOptimizationDatasetInput,
} from "@/lib/rlm-api/optimization";
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
} from "@/lib/rlm-api/ws-client";
export type {
  AuthMeResponse,
  RuntimeConnectivityTestResponse,
  RuntimeSettingsSnapshot,
  RuntimeSettingsUpdateResponse,
  RuntimeStatusResponse,
  ServiceInfoResponse,
} from "@/lib/rlm-api/types";
