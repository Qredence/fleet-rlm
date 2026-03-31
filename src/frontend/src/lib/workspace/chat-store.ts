import { create } from "zustand";
import type { QueryClient } from "@tanstack/react-query";

import {
  createBackendSessionId,
  streamChatOverWs,
  type WsServerMessage,
} from "@/lib/rlm-api";
import { telemetryClient } from "@/lib/telemetry/client";
import { applyWsFrameToMessages } from "@/lib/workspace/backend-chat-event-adapter";
import type {
  ChatMessage,
  ExecutionStep,
} from "@/lib/workspace/workspace-types";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/ws-types";

const DAYTONA_FIRST_FRAME_TIMEOUT_MS = 60_000;

interface StreamMessageOptions {
  traceEnabled?: boolean;
  executionMode?: WsExecutionMode;
  runtimeMode?: WsRuntimeMode;
  repoUrl?: string;
  repoRef?: string;
  contextPaths?: string[];
  batchConcurrency?: number;
}

interface ChatStore {
  messages: ChatMessage[];
  turnArtifactsByMessageId: Record<string, ExecutionStep[]>;
  isStreaming: boolean;
  sessionId: string;
  error: string | null;
  runtimeMode: WsRuntimeMode;
  setSessionId: (id: string) => void;
  resetSession: () => void;
  setRuntimeMode: (mode: WsRuntimeMode) => void;
  setMessages: (
    messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[]),
  ) => void;
  setTurnArtifactsByMessageId: (
    turnArtifactsByMessageId:
      | Record<string, ExecutionStep[]>
      | ((
          prev: Record<string, ExecutionStep[]>,
        ) => Record<string, ExecutionStep[]>),
  ) => void;
  snapshotTurnArtifacts: (messageId: string, steps: ExecutionStep[]) => void;
  clearTurnArtifacts: () => void;
  clearMessages: () => void;
  addMessage: (message: ChatMessage) => void;
  streamController: AbortController | null;
  streamMessage: (
    text: string,
    onFrameCallback?: (frame: WsServerMessage) => void,
    queryClient?: QueryClient,
    options?: StreamMessageOptions,
  ) => Promise<void>;
  stopStreaming: () => void;
}

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  turnArtifactsByMessageId: {},
  isStreaming: false,
  sessionId: createBackendSessionId(),
  error: null,
  runtimeMode: "modal_chat",
  streamController: null,

  setSessionId: (id) => set({ sessionId: id }),
  resetSession: () =>
    set({
      sessionId: createBackendSessionId(),
      messages: [],
      turnArtifactsByMessageId: {},
      isStreaming: false,
      error: null,
    }),
  setRuntimeMode: (runtimeMode) => set({ runtimeMode }),

  setMessages: (updater) =>
    set((state) => ({
      messages:
        typeof updater === "function" ? updater(state.messages) : updater,
    })),

  setTurnArtifactsByMessageId: (updater) =>
    set((state) => ({
      turnArtifactsByMessageId:
        typeof updater === "function"
          ? updater(state.turnArtifactsByMessageId)
          : updater,
    })),

  snapshotTurnArtifacts: (messageId, steps) =>
    set((state) => ({
      turnArtifactsByMessageId: {
        ...state.turnArtifactsByMessageId,
        [messageId]: steps.map((step) => ({ ...step })),
      },
    })),

  clearTurnArtifacts: () => set({ turnArtifactsByMessageId: {} }),
  clearMessages: () => set({ messages: [], turnArtifactsByMessageId: {} }),
  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
    })),

  stopStreaming: () => {
    const { streamController } = get();
    if (streamController) {
      streamController.abort();
    }
    set({ isStreaming: false, streamController: null });
  },

  streamMessage: async (text, onFrameCallback, queryClient, options) => {
    const { sessionId, isStreaming, runtimeMode } = get();

    if (isStreaming || !text.trim()) return;

    const controller = new AbortController();

    set({
      isStreaming: true,
      error: null,
      streamController: controller,
    });

    const traceEnabled = options?.traceEnabled ?? true;
    const resolvedRuntimeMode = options?.runtimeMode ?? runtimeMode;
    const firstFrameTimeoutMs =
      resolvedRuntimeMode === "daytona_pilot"
        ? DAYTONA_FIRST_FRAME_TIMEOUT_MS
        : undefined;

    const payload = {
      type: "message",
      content: text,
      trace: traceEnabled,
      runtime_mode: resolvedRuntimeMode,
      analytics_enabled: telemetryClient.isAnonymousTelemetryEnabled(),
      session_id: sessionId,
      trace_mode: traceEnabled ? "compact" : "off",
    } as const;

    const request = { ...payload } as Record<string, unknown>;
    if (resolvedRuntimeMode === "modal_chat") {
      request.execution_mode = options?.executionMode ?? "auto";
    } else {
      if (options?.repoUrl !== undefined) {
        request.repo_url = options.repoUrl || null;
      }
      if (options?.repoRef !== undefined) {
        const repoUrl = options.repoUrl ?? null;
        request.repo_ref =
          repoUrl && options.repoRef.trim() ? options.repoRef : null;
      }
      if (options?.contextPaths !== undefined) {
        request.context_paths =
          options.contextPaths.length > 0 ? options.contextPaths : null;
      }
      if (options?.batchConcurrency !== undefined) {
        request.batch_concurrency = options.batchConcurrency;
      }
    }

    try {
      await streamChatOverWs(
        request as unknown as Parameters<typeof streamChatOverWs>[0],
        {
          signal: controller.signal,
          firstFrameTimeoutMs,
          onFrame: (frame) => {
            set((state) => ({
              messages: applyWsFrameToMessages(
                state.messages,
                frame,
                queryClient,
              ).messages,
            }));

            if (onFrameCallback) {
              onFrameCallback(frame);
            }
          },
        },
      );
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      const message =
        error instanceof Error ? error.message : "Unknown streaming error";
      set({ error: message });
      throw error;
    } finally {
      if (get().streamController === controller) {
        set({ isStreaming: false, streamController: null });
      }
    }
  },
}));
