import { rlmApiConfig } from "@/lib/rlm-api/config";
import { createWsError } from "@/lib/rlm-api/wsFrameParser";
import {
  createBackendSessionId,
  createReconnectingWs,
} from "@/lib/rlm-api/wsReconnecting";
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
  WsServerError,
  WsServerEvent,
  WsServerMessage,
  WsTraceMode,
} from "@/lib/rlm-api/wsTypes";

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
  return text.replace(/[\u0000-\u001F\u007F]+/g, " ");
}

export async function streamChatOverWs(
  message: WsMessageRequest,
  options: StreamWsOptions,
): Promise<void> {
  if (!rlmApiConfig.wsUrl) {
    throw createWsError("WebSocket URL is not configured (VITE_FLEET_WS_URL)");
  }

  await createReconnectingWs(message, { ...options, url: rlmApiConfig.wsUrl });
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

  createReconnectingWs(null, {
    ...options,
    url: urlObj.toString(),
    signal: controller.signal,
    terminalEventKinds: [],
  }).catch((err: unknown) => {
    console.error("Execution stream error:", sanitizeLogValue(err));
  });

  return () => controller.abort();
}
