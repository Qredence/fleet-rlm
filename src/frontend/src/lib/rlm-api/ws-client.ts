import { rlmApiConfig } from "@/lib/rlm-api/config";
import { createWsError } from "@/lib/rlm-api/ws-frame-parser";
import {
  createBackendSessionId,
  createReconnectingWs,
} from "@/lib/rlm-api/ws-reconnecting";
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

function sanitizeLogValue(value: unknown): string {
  let text: string;
  if (value instanceof Error) {
    text = `${value.name}: ${value.message}`;
  } else if (typeof value === "string") {
    text = value;
  } else {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  // Remove ASCII control characters (including newlines) to prevent log injection.
  let sanitized = "";
  for (const char of text) {
    const code = char.charCodeAt(0);
    sanitized += code <= 0x1f || code === 0x7f ? " " : char;
  }
  return sanitized;
}

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
    console.warn("WebSocket Execution URL is not configured");
    return () => controller.abort();
  }

  const urlObj = new URL(rlmApiConfig.wsExecutionUrl);
  urlObj.searchParams.set("session_id", String(sessionId));
  if (rlmApiConfig.workspaceId) {
    urlObj.searchParams.set("workspace_id", rlmApiConfig.workspaceId);
  }
  if (rlmApiConfig.userId) {
    urlObj.searchParams.set("user_id", rlmApiConfig.userId);
  }

  createReconnectingWs(null, {
    ...options,
    url: urlObj.toString(),
    signal: controller.signal,
    terminalEventKinds: [],
    abortMode: "close",
  }).catch((err: unknown) => {
    console.error("Execution stream error:", sanitizeLogValue(err));
  });

  return () => controller.abort();
}
