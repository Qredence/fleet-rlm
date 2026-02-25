export type WsTraceMode = "compact" | "verbose" | "off";

export type WsConnectionStatus =
  | "connecting"
  | "connected"
  | "disconnected"
  | "reconnecting";

export interface WsConnectionOptions {
  maxRetries?: number; // default 5
  initialBackoff?: number; // default 1000ms
  maxBackoff?: number; // default 30000ms
}

export interface WsMessageRequest {
  type: "message";
  content: string;
  docs_path?: string | null;
  trace?: boolean;
  trace_mode?: WsTraceMode;
  analytics_enabled?: boolean;
  workspace_id?: string;
  user_id?: string;
  session_id?: string;
}

export interface WsCancelRequest {
  type: "cancel";
}

export type WsClientMessage = WsMessageRequest | WsCancelRequest;

export type WsEventKind =
  | "assistant_token"
  | "reasoning_step"
  | "status"
  | "tool_call"
  | "tool_result"
  | "trajectory_step"
  | "final"
  | "error"
  | "cancelled"
  | "plan_update"
  | "rlm_executing"
  | "memory_update";

export interface WsEventPayload {
  kind: WsEventKind;
  text: string;
  payload?: Record<string, unknown>;
  timestamp?: string;
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
