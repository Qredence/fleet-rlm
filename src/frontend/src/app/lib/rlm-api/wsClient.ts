import { rlmApiConfig } from "./config";

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
  | "cancelled";

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

interface StreamWsOptions extends WsConnectionOptions {
  signal?: AbortSignal;
  onFrame: (frame: WsServerMessage) => void;
  onStatusChange?: (status: WsConnectionStatus) => void;
}

function isWsEventKind(value: string): value is WsEventKind {
  return [
    "assistant_token",
    "reasoning_step",
    "status",
    "tool_call",
    "tool_result",
    "trajectory_step",
    "final",
    "error",
    "cancelled",
  ].includes(value);
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return undefined;
  return value as Record<string, unknown>;
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function normalizeExecutionStepKind(
  step: Record<string, unknown>,
): WsEventKind {
  const rawType = String(step.type ?? "")
    .trim()
    .toLowerCase();

  if (rawType === "output") return "final";
  if (rawType === "tool") {
    return step.output == null ? "tool_call" : "tool_result";
  }
  if (rawType === "repl") {
    return step.output == null ? "tool_call" : "tool_result";
  }
  if (rawType === "memory") return "status";
  if (rawType === "llm") {
    return typeof step.output === "string" && step.output.length > 0
      ? "assistant_token"
      : "reasoning_step";
  }

  return "status";
}

function parseExecutionEnvelope(
  parsed: Record<string, unknown>,
): WsServerEvent | null {
  const frameType = String(parsed.type ?? "").trim();
  if (!frameType.startsWith("execution_")) return null;

  if (frameType === "execution_started") {
    return {
      type: "event",
      data: {
        kind: "status",
        text: asText(parsed.message ?? "Execution started"),
        payload: {
          source_type: frameType,
          ...parsed,
        },
        timestamp:
          typeof parsed.timestamp === "string" ? parsed.timestamp : undefined,
      },
    };
  }

  if (frameType === "execution_completed") {
    const payload = asRecord(parsed.payload);
    return {
      type: "event",
      data: {
        kind: "final",
        text: asText(
          parsed.output ?? payload?.output ?? parsed.result ?? parsed.message,
        ),
        payload: {
          source_type: frameType,
          ...(payload ?? {}),
          raw: parsed,
        },
        timestamp:
          typeof parsed.timestamp === "string" ? parsed.timestamp : undefined,
      },
    };
  }

  if (frameType === "execution_step") {
    const nested = asRecord(parsed.data);
    const step = asRecord(parsed.step) ?? asRecord(nested?.step);
    if (!step) {
      return {
        type: "event",
        data: {
          kind: "status",
          text: "Execution step received",
          payload: {
            source_type: frameType,
            raw: parsed,
          },
          timestamp:
            typeof parsed.timestamp === "string" ? parsed.timestamp : undefined,
        },
      };
    }

    const kind = normalizeExecutionStepKind(step);
    const text = asText(
      step.label ??
        step.output ??
        step.input ??
        step.content ??
        step.message ??
        kind,
    );

    return {
      type: "event",
      data: {
        kind,
        text,
        payload: {
          source_type: frameType,
          step,
          raw: parsed,
        },
        timestamp:
          typeof step.timestamp === "string"
            ? step.timestamp
            : typeof parsed.timestamp === "string"
              ? parsed.timestamp
              : undefined,
      },
    };
  }

  return null;
}

function parseWsServerFrame(
  parsed: Record<string, unknown>,
): WsServerMessage | null {
  const frameType = parsed.type;

  if (frameType === "event") {
    const data = asRecord(parsed.data);
    if (!data) return null;

    const kind = String(data.kind ?? "");
    if (!isWsEventKind(kind)) return null;

    return {
      type: "event",
      data: {
        kind,
        text: asText(data.text),
        payload: asRecord(data.payload) ?? undefined,
        timestamp:
          typeof data.timestamp === "string" ? data.timestamp : undefined,
      },
    };
  }

  const executionEnvelope = parseExecutionEnvelope(parsed);
  if (executionEnvelope) return executionEnvelope;

  if (frameType === "error") {
    return {
      type: "error",
      message: asText(parsed.message || "WebSocket server error"),
    };
  }

  return null;
}

function createError(detail: string): Error {
  const error = new Error(detail);
  error.name = "RlmWebSocketError";
  return error;
}

let backendSessionFallbackSequence = 0;

export function createBackendSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  backendSessionFallbackSequence += 1;
  return `fleet-${Date.now()}-${backendSessionFallbackSequence}`;
}

interface RetryState {
  attempt: number;
  backoffTimer: ReturnType<typeof setTimeout> | null;
  aborted: boolean;
}

const DEFAULT_MAX_RETRIES = 5;
const DEFAULT_INITIAL_BACKOFF = 1000;
const DEFAULT_MAX_BACKOFF = 30000;

function calculateBackoff(
  attempt: number,
  initialBackoff: number,
  maxBackoff: number,
): number {
  const backoff = initialBackoff * Math.pow(2, attempt);
  return Math.min(backoff, maxBackoff);
}

async function sleep(ms: number, signal?: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(true), ms);
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Creates a WebSocket connection with automatic reconnection support.
 * Implements exponential backoff with configurable retry limits.
 */
async function createReconnectingWs(
  message: WsMessageRequest,
  options: StreamWsOptions,
): Promise<void> {
  const {
    signal,
    onFrame,
    onStatusChange,
    maxRetries = DEFAULT_MAX_RETRIES,
    initialBackoff = DEFAULT_INITIAL_BACKOFF,
    maxBackoff = DEFAULT_MAX_BACKOFF,
  } = options;

  const retryState: RetryState = {
    attempt: 0,
    backoffTimer: null,
    aborted: false,
  };

  const updateStatus = (status: WsConnectionStatus) => {
    onStatusChange?.(status);
  };

  const cleanup = () => {
    if (retryState.backoffTimer) {
      clearTimeout(retryState.backoffTimer);
      retryState.backoffTimer = null;
    }
  };

  const attemptConnection = async (): Promise<void> => {
    return new Promise<void>((resolve, reject) => {
      let settled = false;
      let completed = false;

      updateStatus(retryState.attempt > 0 ? "reconnecting" : "connecting");

      const socket = new WebSocket(rlmApiConfig.wsUrl!);

      const finish = (fn: () => void) => {
        if (settled) return;
        settled = true;
        fn();
      };

      const safeClose = () => {
        if (
          socket.readyState === WebSocket.OPEN ||
          socket.readyState === WebSocket.CONNECTING
        ) {
          socket.close();
        }
      };

      const abortHandler = () => {
        retryState.aborted = true;
        cleanup();

        if (socket.readyState === WebSocket.OPEN) {
          const cancel: WsCancelRequest = { type: "cancel" };
          socket.send(JSON.stringify(cancel));
        }

        safeClose();
        updateStatus("disconnected");
        finish(resolve);
      };

      if (signal) {
        if (signal.aborted) {
          abortHandler();
          return;
        }
        signal.addEventListener("abort", abortHandler, { once: true });
      }

      socket.addEventListener("open", () => {
        updateStatus("connected");
        socket.send(JSON.stringify(message));
      });

      socket.addEventListener("message", (event) => {
        try {
          const parsed = JSON.parse(String(event.data)) as Record<
            string,
            unknown
          >;
          const frame = parseWsServerFrame(parsed);
          if (!frame) return;

          onFrame(frame);

          if (frame.type === "error") {
            completed = true;
            safeClose();
            updateStatus("disconnected");
            finish(() => reject(createError(frame.message)));
            return;
          }

          const kind = frame.data.kind;
          if (kind === "final" || kind === "cancelled") {
            completed = true;
            safeClose();
            updateStatus("disconnected");
            finish(resolve);
          } else if (kind === "error") {
            completed = true;
            safeClose();
            updateStatus("disconnected");
            finish(() =>
              reject(createError(frame.data.text || "Server stream error")),
            );
          }
        } catch {
          // Ignore malformed frames to avoid taking down the stream.
        }
      });

      socket.addEventListener("error", () => {
        safeClose();
        // Don't reject here - let the close handler deal with reconnection
      });

      socket.addEventListener("close", async (event) => {
        if (settled) return;

        // Clean closure cases - no retry needed
        if (completed || retryState.aborted || event.code === 1000) {
          updateStatus("disconnected");
          finish(resolve);
          return;
        }

        // Check if we can retry
        if (retryState.attempt >= maxRetries) {
          updateStatus("disconnected");
          finish(() =>
            reject(
              createError(
                `WebSocket connection failed after ${maxRetries} retries`,
              ),
            ),
          );
          return;
        }

        // Attempt reconnection with exponential backoff
        retryState.attempt += 1;
        const backoffMs = calculateBackoff(
          retryState.attempt - 1,
          initialBackoff,
          maxBackoff,
        );

        const shouldContinue = await sleep(backoffMs, signal);
        if (!shouldContinue || retryState.aborted) {
          updateStatus("disconnected");
          finish(resolve);
          return;
        }

        // Recursively attempt reconnection
        try {
          await attemptConnection();
          finish(resolve);
        } catch (err) {
          finish(() => reject(err));
        }
      });
    });
  };

  try {
    await attemptConnection();
  } finally {
    cleanup();
  }
}

export async function streamChatOverWs(
  message: WsMessageRequest,
  options: StreamWsOptions,
): Promise<void> {
  if (!rlmApiConfig.wsUrl) {
    throw createError("WebSocket URL is not configured (VITE_FLEET_WS_URL)");
  }

  await createReconnectingWs(message, options);
}
