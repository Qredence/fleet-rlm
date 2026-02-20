/**
 * Chat hook for the skill creation flow.
 *
 * Manages sending messages and receiving streaming responses from the
 * fleet-rlm chat endpoint. In mock mode, delegates to the existing
 * `useChatSimulation` hook (which drives the Phase 1/2/3 demo flow).
 *
 * In API mode, uses SSE streaming to progressively update the message
 * list as content arrives from the backend.
 *
 * @example
 * ```tsx
 * const { sendMessage, isStreaming } = useChat(sessionId);
 *
 * await sendMessage('Create a test generation skill');
 * ```
 */
import { useState, useCallback, useRef } from "react";
import { isMockMode } from "../../lib/api/config";
import { chatEndpoints } from "../../lib/api/endpoints";
import { createLocalId } from "../../lib/id";
import type { ChatMessage } from "../data/types";

// ── Types ───────────────────────────────────────────────────────────

interface UseChatReturn {
  /** Send a message and begin streaming the response */
  sendMessage: (content: string, sessionId: string) => Promise<void>;
  /** True while the assistant response is streaming */
  isStreaming: boolean;
  /** The current streaming message content (progressively updated) */
  streamingContent: string;
  /** Abort the current stream */
  abortStream: () => void;
  /** Error from the last send attempt */
  error: Error | null;
}

// ── Hook ────────────────────────────────────────────────────────────

export function useChat(): UseChatReturn {
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const abortStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  const sendMessage = useCallback(
    async (content: string, sessionId: string) => {
      // Call isMockMode() inside the callback to avoid capturing a stale value.
      // While isMockMode() currently returns a build-time constant, calling it
      // here ensures correctness if it ever becomes dynamic (e.g., runtime config).
      if (isMockMode()) {
        // In mock mode, the chat simulation is handled by useChatSimulation
        // in SkillCreationFlow. This hook is a no-op in mock mode.
        return;
      }

      setError(null);
      setIsStreaming(true);
      setStreamingContent("");

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const stream = chatEndpoints.stream(
          { sessionId, message: content },
          controller.signal,
        );

        let accumulated = "";

        for await (const event of stream) {
          if (controller.signal.aborted) break;

          switch (event.event) {
            case "content_delta": {
              const delta =
                ((event.data as Record<string, unknown>).content as string) ||
                "";
              accumulated += delta;
              setStreamingContent(accumulated);
              break;
            }
            case "content_complete":
            case "done": {
              setIsStreaming(false);
              break;
            }
            case "error": {
              const errorMsg =
                ((event.data as Record<string, unknown>).message as string) ||
                "Stream error";
              throw new Error(errorMsg);
            }
            // Phase changes, HITL requests, etc. will be handled
            // when we integrate the creation flow more deeply
            default:
              break;
          }
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setError(err as Error);
        }
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [], // No dependencies: isMockMode is a pure function of build-time env vars,
        // and React state setters (setError, setIsStreaming, setStreamingContent) are stable.
  );

  return {
    sendMessage,
    isStreaming,
    streamingContent,
    abortStream,
    error,
  };
}

// ── Helper: Build ChatMessage from stream events ────────────────────

export function createChatMessage(
  type: ChatMessage["type"],
  content: string,
  phase?: 1 | 2 | 3,
): ChatMessage {
  return {
    id: createLocalId("msg"),
    type,
    content,
    phase,
  };
}
