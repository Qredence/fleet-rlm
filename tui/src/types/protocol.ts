/**
 * TypeScript protocol types mirroring Python models.py
 * Used for WebSocket communication between OpenTUI frontend and FastAPI backend.
 */

/** Connection state */
export type ConnectionState =
  | "disconnected"
  | "connecting"
  | "connected"
  | "error";

/** Trace mode for agent execution */
export type TraceMode = "compact" | "verbose" | "off";

/** Stream event kinds emitted by the agent */
export type StreamEventKind =
  | "assistant_token"
  | "status"
  | "reasoning_step"
  | "tool_call"
  | "tool_result"
  | "final"
  | "error"
  | "cancelled";

/** Streaming event from agent to UI */
export interface StreamEvent {
  kind: StreamEventKind;
  text: string;
  payload: Record<string, unknown>;
  timestamp: string; // ISO 8601 format
}

/** Accumulated turn state for rendering */
export interface TurnState {
  assistantTokens: string[];
  transcriptText: string;
  reasoningLines: string[];
  toolTimeline: string[];
  statusLines: string[];
  streamChunks: string[];
  thoughtChunks: string[];
  statusMessages: string[];
  trajectory: Record<string, unknown>;
  finalText: string;
  historyTurns: number;
  tokenCount: number;
  cancelled: boolean;
  errored: boolean;
  done: boolean;
  errorMessage: string;
}

/** WebSocket message types (client -> server) */
export type ClientMessageType = "message" | "cancel" | "command";

/** Client chat/cancel message to server */
export interface ClientMessage {
  type: "message" | "cancel";
  content?: string;
  docs_path?: string;
  trace?: boolean;
  trace_mode?: TraceMode;
}

/** Client command message to server */
export interface ClientCommandMessage {
  type: "command";
  command: string;
  args: Record<string, unknown>;
}

/** WebSocket message types (server -> client) */
export type ServerMessageType = "event" | "error" | "command_result";

/** Server streaming/error message to client */
export interface ServerMessage {
  type: "event" | "error";
  data?: StreamEvent;
  message?: string;
}

/** Server command result message to client */
export interface ServerCommandResult {
  type: "command_result";
  command: string;
  result: Record<string, unknown>;
}

/** Union of all server message types */
export type ServerPayload = ServerMessage | ServerCommandResult;

/** Session configuration */
export interface SessionConfig {
  profileName: string;
  docsPath: string | null;
  secretName: string;
  volumeName: string | null;
  timeout: number;
  reactMaxIters: number;
  rlmMaxIterations: number;
  rlmMaxLlmCalls: number;
  trace: boolean;
  traceMode: TraceMode;
  stream: boolean;
  streamRefreshMs: number;
}

/** Transcript event for session history */
export interface TranscriptEvent {
  role: "user" | "assistant" | "system" | "trace" | "status";
  content: string;
  payload?: Record<string, unknown> | null;
}
