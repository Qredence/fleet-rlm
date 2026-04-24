import { rlmApiConfig } from "@/lib/rlm-api/config";
import { createWsError } from "@/lib/rlm-api/ws-frame-parser";
import { createBackendSessionId, createReconnectingWs } from "@/lib/rlm-api/ws-reconnecting";
import type {
  WsCommandRequest,
  StreamWsOptions,
  WsCancelRequest,
  WsClientMessage,
  WsConnectionOptions,
  WsConnectionStatus,
  WsEventKind,
  WsEventPayload,
  WsMessageRequest,
  WsRuntimeMode,
  WsServerError,
  WsServerEvent,
  WsServerMessage,
  WsTraceMode,
} from "@/lib/rlm-api/ws-types";

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
};

export { createBackendSessionId };

export async function streamChatOverWs(
  message: WsMessageRequest,
  options: StreamWsOptions,
): Promise<void> {
  if (!rlmApiConfig.wsUrl) {
    throw createWsError("WebSocket URL is not configured (VITE_FLEET_WS_URL)");
  }

  await createReconnectingWs(message, {
    ...options,
    url: rlmApiConfig.wsUrl,
    abortMode: "cancel",
  });
}

export async function sendCommandOverWs(
  message: WsCommandRequest,
  options: StreamWsOptions,
): Promise<void> {
  if (!rlmApiConfig.wsUrl) {
    throw createWsError("WebSocket URL is not configured (VITE_FLEET_WS_URL)");
  }

  await createReconnectingWs(message, {
    ...options,
    url: rlmApiConfig.wsUrl,
    maxRetries: options.maxRetries ?? 0,
    terminalEventKinds: ["command_ack", "command_reject"],
  });
}

export function subscribeToExecutionStream(
  sessionId: string | number,
  options: Omit<StreamWsOptions, "signal" | "onStatusChange">,
): () => void {
  const controller = new AbortController();

  if (!rlmApiConfig.wsExecutionUrl) {
    // WebSocket Execution URL is not configured — return no-op cleanup.
    return () => controller.abort();
  }

  const urlObj = new URL(rlmApiConfig.wsExecutionUrl);
  urlObj.searchParams.set("session_id", String(sessionId));

  createReconnectingWs(null, {
    ...options,
    url: urlObj.toString(),
    signal: controller.signal,
    terminalEventKinds: [],
    abortMode: "close",
  }).catch(() => {
    // Execution stream error swallowed silently to prevent unhandled rejection.
  });

  return () => controller.abort();
}
