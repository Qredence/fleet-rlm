import { create } from "zustand";
import {
  streamChatOverWs,
  type WsMessageRequest,
  type WsServerMessage,
  createBackendSessionId,
  rlmApiConfig,
} from "@/lib/rlm-api";
import type { ChatMessage } from "@/lib/data/types";
import { applyWsFrameToMessages } from "@/features/rlm-workspace/backendChatEventAdapter";
import { telemetryClient } from "@/lib/telemetry/client";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/wsTypes";
import { QueryClient } from "@tanstack/react-query";
import type { ExecutionStep } from "@/stores/artifactStore";

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
  // State
  messages: ChatMessage[];
  turnArtifactsByMessageId: Record<string, ExecutionStep[]>;
  isStreaming: boolean;
  sessionId: string;
  error: string | null;
  runtimeMode: WsRuntimeMode;

  // Actions
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

  // Streaming
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

  streamMessage: async (
    text: string,
    onFrameCallback?: (frame: WsServerMessage) => void,
    queryClient?: QueryClient,
    options?: StreamMessageOptions,
  ) => {
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

    const payload: WsMessageRequest = {
      type: "message",
      content: text,
      trace: traceEnabled,
      runtime_mode: resolvedRuntimeMode,
      analytics_enabled: telemetryClient.isAnonymousTelemetryEnabled(),
      workspace_id: rlmApiConfig.workspaceId,
      user_id: rlmApiConfig.userId,
      session_id: sessionId,
      trace_mode: traceEnabled ? "compact" : "off",
    };
    if (resolvedRuntimeMode === "modal_chat") {
      payload.execution_mode = options?.executionMode ?? "auto";
    } else {
      if (options?.repoUrl !== undefined) {
        payload.repo_url = options.repoUrl || null;
      }
      if (options?.repoRef !== undefined) {
        const repoUrl = options.repoUrl ?? null;
        payload.repo_ref =
          repoUrl && options.repoRef.trim() ? options.repoRef : null;
      }
      if (options?.contextPaths !== undefined) {
        payload.context_paths =
          options.contextPaths.length > 0 ? options.contextPaths : null;
      }
      if (options?.batchConcurrency !== undefined) {
        payload.batch_concurrency = options.batchConcurrency;
      }
    }

    try {
      await streamChatOverWs(payload, {
        signal: controller.signal,
        onFrame: (frame) => {
          set((state) => ({
            messages: applyWsFrameToMessages(state.messages, frame, queryClient)
              .messages,
          }));

          if (onFrameCallback) {
            onFrameCallback(frame);
          }
        },
      });
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      const message =
        error instanceof Error ? error.message : "Unknown streaming error";
      set({ error: message });
      throw error;
    } finally {
      // Only set isStreaming to false if we are still the active controller
      if (get().streamController === controller) {
        set({ isStreaming: false, streamController: null });
      }
    }
  },
}));
