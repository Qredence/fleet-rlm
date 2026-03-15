export type WsTraceMode = "compact" | "verbose" | "off";

export type WsExecutionMode = "auto" | "rlm_only" | "tools_only";
export type WsRuntimeMode = "modal_chat" | "daytona_pilot";

export type WsConnectionStatus = "connecting" | "connected" | "disconnected" | "reconnecting";

export interface WsConnectionOptions {
  maxRetries?: number; // default 5
  initialBackoff?: number; // default 1000ms
  maxBackoff?: number; // default 30000ms
  firstFrameTimeoutMs?: number; // default 15000ms for request/response streams
}

export interface WsMessageRequest {
  type: "message";
  content: string;
  docs_path?: string | null;
  trace?: boolean;
  trace_mode?: WsTraceMode;
  execution_mode?: WsExecutionMode;
  runtime_mode?: WsRuntimeMode;
  repo_url?: string | null;
  repo_ref?: string | null;
  context_paths?: string[] | null;
  batch_concurrency?: number | null;
  analytics_enabled?: boolean;
  workspace_id?: string;
  user_id?: string;
  session_id?: string;
}

export interface WsCancelRequest {
  type: "cancel";
}

export interface WsCommandRequest {
  type: "command";
  command: string;
  args?: Record<string, unknown>;
  workspace_id?: string;
  user_id?: string;
  session_id?: string;
}

export type WsClientMessage = WsMessageRequest | WsCancelRequest | WsCommandRequest;

export type WsEventKind =
  | "assistant_token"
  | "reasoning_step"
  | "status"
  | "warning"
  | "tool_call"
  | "tool_result"
  | "trajectory_step"
  | "final"
  | "error"
  | "cancelled"
  | "plan_update"
  | "rlm_executing"
  | "memory_update"
  | "hitl_request"
  | "hitl_resolved"
  | "command_ack"
  | "command_reject";

export interface WsEventPayload {
  kind: WsEventKind;
  text: string;
  payload?: Record<string, unknown>;
  timestamp?: string;
  version?: number;
  event_id?: string;
}

/** Runtime context snapshot attached to enriched events by the backend. */
export interface WsRuntimeContext {
  depth: number;
  max_depth: number;
  execution_profile: string;
  sandbox_active: boolean;
  effective_max_iters: number;
  volume_name?: string;
  execution_mode?: string;
  runtime_mode?: string;
  daytona_mode?: string;
  sandbox_id?: string;
}

export interface WsServerEvent {
  type: "event";
  data: WsEventPayload;
}

export interface WsServerError {
  type: "error";
  message: string;
}

export type WsServerMessage = WsServerEvent | WsServerError;

export interface StreamWsOptions extends WsConnectionOptions {
  signal?: AbortSignal;
  onFrame: (frame: WsServerMessage) => void;
  onStatusChange?: (status: WsConnectionStatus) => void;
}
