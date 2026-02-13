/**
 * WebSocket hook for fleet-rlm backend communication.
 * Handles connection, reconnection, message routing, and command dispatch.
 */

import { useEffect, useRef, useState, useCallback } from "react";
import type {
  ClientMessage,
  ClientCommandMessage,
  ServerMessage,
  ServerCommandResult,
  ServerPayload,
  StreamEvent,
  ConnectionState,
  TraceMode,
} from "../types/protocol";

export interface UseWebSocketOptions {
  url: string;
  autoConnect?: boolean;
  reconnectDelay?: number;
  maxReconnectAttempts?: number;
  workspaceId?: string;
  userId?: string;
  sessionId?: string | null;
  onEvent?: (event: StreamEvent) => void;
  onError?: (error: string) => void;
  onConnectionChange?: (state: ConnectionState) => void;
  onCommandResult?: (command: string, result: Record<string, unknown>) => void;
}

export interface UseWebSocketReturn {
  connectionState: ConnectionState;
  sendMessage: (
    content: string,
    docsPath?: string,
    traceMode?: TraceMode,
  ) => void;
  sendCancel: () => void;
  sendCommand: (command: string, args: Record<string, unknown>) => void;
  connect: () => void;
  disconnect: () => void;
  reconnect: () => void;
}

export function useWebSocket(options: UseWebSocketOptions): UseWebSocketReturn {
  const {
    url,
    autoConnect = true,
    reconnectDelay = 2000,
    maxReconnectAttempts = 5,
    workspaceId = "default",
    userId = "anonymous",
    sessionId = null,
    onEvent,
    onError,
    onConnectionChange,
    onCommandResult,
  } = options;

  const [connectionState, setConnectionState] =
    useState<ConnectionState>("disconnected");

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<Timer | null>(null);
  const shouldReconnectRef = useRef(autoConnect);

  // Latest ref pattern: keep refs updated on every render
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  const onConnectionChangeRef = useRef(onConnectionChange);
  const onCommandResultRef = useRef(onCommandResult);

  // Update refs when callbacks change (runs after every render)
  useEffect(() => {
    onEventRef.current = onEvent;
    onErrorRef.current = onError;
    onConnectionChangeRef.current = onConnectionChange;
    onCommandResultRef.current = onCommandResult;
  });

  const updateConnectionState = useCallback(
    (state: ConnectionState) => {
      setConnectionState(state);
      onConnectionChangeRef.current?.(state);
    },
    [onConnectionChangeRef],
  );

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return; // Already connected
    }

    updateConnectionState("connecting");
    shouldReconnectRef.current = true;

    try {
      const ws = new WebSocket(url);

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        updateConnectionState("connected");
      };

      ws.onmessage = (event) => {
        try {
          const message: ServerPayload = JSON.parse(event.data.toString());

          if (message.type === "event" && "data" in message && message.data) {
            onEventRef.current?.(message.data);
          } else if (
            message.type === "error" &&
            "message" in message &&
            message.message
          ) {
            onErrorRef.current?.(message.message);
          } else if (message.type === "command_result") {
            const cmdResult = message as ServerCommandResult;
            onCommandResultRef.current?.(
              cmdResult.command,
              cmdResult.result,
            );
          }
        } catch (err) {
          onErrorRef.current?.(
            `Failed to parse message: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      };

      ws.onerror = (error) => {
        updateConnectionState("error");
        onErrorRef.current?.(`WebSocket error: ${"Connection error"}`);
      };

      ws.onclose = () => {
        wsRef.current = null;
        updateConnectionState("disconnected");

        // Attempt reconnection if enabled
        if (
          shouldReconnectRef.current &&
          reconnectAttemptsRef.current < maxReconnectAttempts
        ) {
          reconnectAttemptsRef.current += 1;
          reconnectTimeoutRef.current = setTimeout(() => {
            connect();
          }, reconnectDelay);
        }
      };

      wsRef.current = ws;
    } catch (err) {
      updateConnectionState("error");
      onErrorRef.current?.(
        `Failed to create WebSocket: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }, [url, reconnectDelay, maxReconnectAttempts, updateConnectionState]);

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false;

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setConnectionState("disconnected");
    onConnectionChangeRef.current?.("disconnected");
  }, []);

  const reconnect = useCallback(() => {
    disconnect();
    reconnectAttemptsRef.current = 0;
    shouldReconnectRef.current = true;
    connect();
  }, [connect, disconnect]);

  const sendMessage = useCallback(
    (content: string, docsPath?: string, traceMode?: TraceMode) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) {
        onErrorRef.current?.("Cannot send message: WebSocket not connected");
        return;
      }

      const message: ClientMessage = {
        type: "message",
        content,
        docs_path: docsPath,
        trace: traceMode !== "off",
        trace_mode: traceMode,
        workspace_id: workspaceId,
        user_id: userId,
        session_id: sessionId,
      };

      try {
        wsRef.current.send(JSON.stringify(message));
      } catch (err) {
        onErrorRef.current?.(
          `Failed to send message: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    [workspaceId, userId, sessionId],
  );

  const sendCancel = useCallback(() => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) {
      return;
    }

    const message: ClientMessage = {
      type: "cancel",
      workspace_id: workspaceId,
      user_id: userId,
      session_id: sessionId,
    };

    try {
      wsRef.current.send(JSON.stringify(message));
    } catch (err) {
      onErrorRef.current?.(
        `Failed to send cancel: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }, [workspaceId, userId, sessionId]);

  const sendCommand = useCallback(
    (command: string, args: Record<string, unknown>) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) {
        onErrorRef.current?.("Cannot send command: WebSocket not connected");
        return;
      }

      const message: ClientCommandMessage = {
        type: "command",
        command,
        args,
        workspace_id: workspaceId,
        user_id: userId,
        session_id: sessionId,
      };

      try {
        wsRef.current.send(JSON.stringify(message));
      } catch (err) {
        onErrorRef.current?.(
          `Failed to send command: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    [workspaceId, userId, sessionId],
  );

  // Auto-connect on mount
  useEffect(() => {
    if (autoConnect) {
      connect();
    }

    return () => {
      // Cleanup reconnect timeout on unmount
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [autoConnect]);

  return {
    connectionState,
    sendMessage,
    sendCancel,
    sendCommand,
    connect,
    disconnect,
    reconnect,
  };
}
