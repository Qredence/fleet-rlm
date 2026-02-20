import { create } from "zustand";
import {
  streamChatOverWs,
  type WsMessageRequest,
  type WsServerMessage,
  createBackendSessionId,
  rlmApiConfig,
} from "@/lib/rlm-api";
import type { ChatMessage } from "@/lib/data/types";
import { applyWsFrameToMessages } from "@/app/pages/skill-creation/backendChatEventAdapter";

interface ChatStore {
  // State
  messages: ChatMessage[];
  isStreaming: boolean;
  sessionId: string;
  error: string | null;

  // Actions
  setSessionId: (id: string) => void;
  resetSession: () => void;
  setMessages: (
    messages: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[]),
  ) => void;
  clearMessages: () => void;
  addMessage: (message: ChatMessage) => void;

  // Streaming
  streamController: AbortController | null;
  streamMessage: (
    text: string,
    onFrameCallback?: (frame: WsServerMessage) => void,
  ) => Promise<void>;
  stopStreaming: () => void;
}

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  isStreaming: false,
  sessionId: createBackendSessionId(),
  error: null,
  streamController: null,

  setSessionId: (id) => set({ sessionId: id }),
  resetSession: () =>
    set({
      sessionId: createBackendSessionId(),
      messages: [],
      isStreaming: false,
      error: null,
    }),

  setMessages: (updater) =>
    set((state) => ({
      messages:
        typeof updater === "function" ? updater(state.messages) : updater,
    })),

  clearMessages: () => set({ messages: [] }),

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
  ) => {
    const { sessionId, isStreaming } = get();

    if (isStreaming || !text.trim()) return;

    const controller = new AbortController();

    set({
      isStreaming: true,
      error: null,
      streamController: controller,
    });

    const payload: WsMessageRequest = {
      type: "message",
      content: text,
      trace: rlmApiConfig.trace,
      workspace_id: rlmApiConfig.workspaceId,
      user_id: rlmApiConfig.userId,
      session_id: sessionId,
      trace_mode: "compact",
    };

    try {
      await streamChatOverWs(payload, {
        signal: controller.signal,
        onFrame: (frame) => {
          set((state) => ({
            messages: applyWsFrameToMessages(state.messages, frame).messages,
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
