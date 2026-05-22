import { create } from "zustand";
import type { QueryClient } from "@tanstack/react-query";

import { createBackendSessionId, streamChatOverWs, type WsServerMessage } from "@/lib/rlm-api";
import { applyWsFrameToMessages } from "@/lib/workspace/backend-chat-event-adapter";
import type { ChatMessage, ExecutionStep } from "@/lib/workspace/workspace-types";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/ws-types";

const DAYTONA_FIRST_FRAME_TIMEOUT_MS = 60_000;
const STREAM_FRAME_FLUSH_FALLBACK_MS = 32;

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
  setMessages: (messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => void;
  setTurnArtifactsByMessageId: (
    turnArtifactsByMessageId:
      | Record<string, ExecutionStep[]>
      | ((prev: Record<string, ExecutionStep[]>) => Record<string, ExecutionStep[]>),
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
  runtimeMode: "daytona_pilot",
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
      messages: typeof updater === "function" ? updater(state.messages) : updater,
    })),

  setTurnArtifactsByMessageId: (updater) =>
    set((state) => ({
      turnArtifactsByMessageId:
        typeof updater === "function" ? updater(state.turnArtifactsByMessageId) : updater,
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
    const { sessionId, isStreaming } = get();

    if (isStreaming || !text.trim()) return;

    const controller = new AbortController();

    set({
      isStreaming: true,
      error: null,
      streamController: controller,
    });

    const traceEnabled = options?.traceEnabled ?? true;
    const firstFrameTimeoutMs = DAYTONA_FIRST_FRAME_TIMEOUT_MS;

    const payload = {
      type: "message",
      content: text,
      trace: traceEnabled,
      session_id: sessionId,
      trace_mode: traceEnabled ? "compact" : "off",
      execution_mode: options?.executionMode ?? "auto",
    } as const;

    const request = { ...payload } as Record<string, unknown>;
    if (options?.repoUrl !== undefined) {
      request.repo_url = options.repoUrl || null;
    }
    if (options?.repoRef !== undefined) {
      const repoUrl = options.repoUrl ?? null;
      request.repo_ref = repoUrl && options.repoRef.trim() ? options.repoRef : null;
    }
    if (options?.contextPaths !== undefined) {
      request.context_paths = options.contextPaths.length > 0 ? options.contextPaths : null;
    }
    if (options?.batchConcurrency !== undefined) {
      request.batch_concurrency = options.batchConcurrency;
    }

    // Collect frames arriving within the same animation frame and apply them in one
    // Zustand update, reducing React reconciliations during high-throughput streaming.
    // Fall back to a short timer so streaming still paints while the page is throttled.
    // oxlint-disable-next-line prefer-const -- array is mutated via splice
    let pendingFrames: WsServerMessage[] = [];
    let rafScheduled = false;
    let scheduledFrameId: number | null = null;
    let scheduledFlushTimeout: ReturnType<typeof setTimeout> | null = null;

    const clearScheduledFlush = () => {
      if (scheduledFrameId != null && typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(scheduledFrameId);
        scheduledFrameId = null;
      }
      if (scheduledFlushTimeout != null) {
        clearTimeout(scheduledFlushTimeout);
        scheduledFlushTimeout = null;
      }
    };

    const flushFrames = () => {
      clearScheduledFlush();
      rafScheduled = false;
      const frames = pendingFrames.splice(0);
      if (frames.length === 0) return;
      set((state) => ({
        messages: frames.reduce(
          (msgs, f) => applyWsFrameToMessages(msgs, f, queryClient).messages,
          state.messages,
        ),
      }));
      if (onFrameCallback) {
        frames.forEach((f) => onFrameCallback(f));
      }
    };

    const scheduleFrameFlush = () => {
      if (rafScheduled) return;
      rafScheduled = true;

      if (typeof requestAnimationFrame === "function") {
        scheduledFrameId = requestAnimationFrame(() => {
          scheduledFrameId = null;
          flushFrames();
        });
        scheduledFlushTimeout = setTimeout(flushFrames, STREAM_FRAME_FLUSH_FALLBACK_MS);
        return;
      }

      scheduledFlushTimeout = setTimeout(flushFrames, 0);
    };

    try {
      await streamChatOverWs(request as unknown as Parameters<typeof streamChatOverWs>[0], {
        signal: controller.signal,
        firstFrameTimeoutMs,
        onFrame: (frame) => {
          pendingFrames.push(frame);
          scheduleFrameFlush();
        },
      });
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      const message = error instanceof Error ? error.message : "Unknown streaming error";
      set({ error: message });
      throw error;
    } finally {
      // Flush any frames that arrived after the last rAF tick but before the stream ended.
      flushFrames();
      if (get().streamController === controller) {
        set({ isStreaming: false, streamController: null });
      }
    }
  },
}));
