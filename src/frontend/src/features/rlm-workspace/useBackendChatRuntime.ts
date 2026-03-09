import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useNavigationStore } from "@/stores/navigationStore";
import type { Conversation } from "@/stores/chatHistoryStore";
import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import { applyWsFrameToMessages } from "@/features/rlm-workspace/backendChatEventAdapter";
import { applyWsFrameToArtifacts } from "@/features/rlm-workspace/backendArtifactEventAdapter";
import type {
  ChatRuntime,
  ChatSubmitOptions,
} from "@/features/rlm-workspace/runtime-types";
import { useArtifactStore } from "@/stores/artifactStore";
import { useChatStore } from "@/stores/chatStore";
import {
  sendCommandOverWs,
  rlmApiConfig,
  type WsServerMessage,
  subscribeToExecutionStream,
} from "@/lib/rlm-api";
import { useQueryClient } from "@tanstack/react-query";

function isTerminalFrame(frame: WsServerMessage): boolean {
  if (frame.type === "error") return true;
  return (
    frame.data.kind === "final" ||
    frame.data.kind === "cancelled" ||
    frame.data.kind === "error"
  );
}

function isErrorFrame(frame: WsServerMessage): boolean {
  return frame.type === "error" || frame.data.kind === "error";
}

let localMessageSequence = 0;

function createLocalMessageId(prefix: string): string {
  localMessageSequence += 1;
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${localMessageSequence}`;
}

function toUserMessage(content: string): ChatMessage {
  return {
    id: createLocalMessageId("user"),
    type: "user",
    content,
  };
}

function applyOptimisticHitlResolution(
  messages: ChatMessage[],
  msgId: string,
  actionLabel: string,
): ChatMessage[] {
  return messages.map((message) => {
    if (message.id !== msgId || message.type !== "hitl" || !message.hitlData) {
      return message;
    }
    return {
      ...message,
      hitlData: {
        ...message.hitlData,
        resolved: true,
        resolvedLabel: actionLabel,
      },
    };
  });
}

function revertOptimisticHitlResolution(
  messages: ChatMessage[],
  msgId: string,
): ChatMessage[] {
  return messages.map((message) => {
    if (message.id !== msgId || message.type !== "hitl" || !message.hitlData) {
      return message;
    }
    return {
      ...message,
      hitlData: {
        ...message.hitlData,
        resolved: false,
        resolvedLabel: undefined,
      },
    };
  });
}

export function useBackendChatRuntime(): ChatRuntime {
  const {
    setCreationPhase,
    sessionId: navSessionId,
    isCanvasOpen,
    openCanvas,
  } = useNavigationStore();
  const clearArtifactSteps = useArtifactStore((state) => state.clear);

  const {
    messages,
    isStreaming,
    sessionId,
    streamMessage,
    stopStreaming,
    resetSession,
    setMessages,
    addMessage,
  } = useChatStore();

  const queryClient = useQueryClient();

  const [inputValue, setInputValue] = useState("");
  const [phase, setPhase] = useState<CreationPhase>("idle");
  const [isTyping, setIsTyping] = useState(false);

  const isFirstMount = useRef(true);

  const resetRuntime = useCallback(() => {
    stopStreaming();
    resetSession();
    setInputValue("");
    setPhase("idle");
    setCreationPhase("idle");
    setIsTyping(false);
    clearArtifactSteps();
  }, [clearArtifactSteps, setCreationPhase, stopStreaming, resetSession]);

  useEffect(() => {
    if (isFirstMount.current) {
      isFirstMount.current = false;
      return;
    }

    resetRuntime();
  }, [navSessionId, resetRuntime]);

  useEffect(() => {
    if (!navSessionId) return;

    const unsubscribe = subscribeToExecutionStream(navSessionId, {
      onFrame: (frame) => applyWsFrameToArtifacts(frame),
    });

    return () => unsubscribe();
  }, [navSessionId]);

  const onFrame = useCallback(
    (frame: WsServerMessage) => {
      // Any backend frame means the server started responding.
      setIsTyping(false);

      applyWsFrameToArtifacts(frame);

      if (isTerminalFrame(frame)) {
        if (isErrorFrame(frame)) {
          setPhase("idle");
          setCreationPhase("idle");
        } else {
          setPhase("complete");
          setCreationPhase("complete");
        }
      }
    },
    [setCreationPhase],
  );

  const handleSubmit = useCallback(
    async (options?: ChatSubmitOptions) => {
      const text = inputValue.trim();
      if (!text || isTyping || isStreaming) return;

      if ((options?.attachments?.length ?? 0) > 0) {
        toast("Attachments added locally", {
          description:
            "This backend currently does not accept binary upload payloads. Send continues with text only.",
        });
      }

      setInputValue("");
      addMessage(toUserMessage(text));
      setPhase("understanding");
      setCreationPhase("understanding");
      setIsTyping(true);
      clearArtifactSteps();
      if (!isCanvasOpen) openCanvas();

      let terminalSeen = false;

      try {
        await streamMessage(
          text,
          (frame) => {
            if (isTerminalFrame(frame)) terminalSeen = true;
            onFrame(frame);
          },
          queryClient,
          {
            traceEnabled: true,
            executionMode: options?.executionMode,
          },
        );
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Unknown streaming error";
        if (!terminalSeen) {
          applyWsFrameToArtifacts({ type: "error", message });
          setMessages((prev) => [
            ...prev,
            {
              id: createLocalMessageId("sys"),
              type: "system",
              content: `Backend error: ${message}`,
              phase: 1,
            },
          ]);
          setPhase("idle");
          setCreationPhase("idle");
        }
        toast.error("Backend stream failed", { description: message });
      } finally {
        setIsTyping(false);
        if (!terminalSeen) {
          setPhase("idle");
          setCreationPhase("idle");
        }
      }
    },
    [
      clearArtifactSteps,
      inputValue,
      isCanvasOpen,
      isTyping,
      isStreaming,
      onFrame,
      openCanvas,
      queryClient,
      streamMessage,
      setCreationPhase,
      addMessage,
      setMessages,
    ],
  );

  const resolveHitl = useCallback(
    async (msgId: string, actionLabel: string) => {
      const label = actionLabel.trim();
      if (!label) return;

      setMessages((prev) => applyOptimisticHitlResolution(prev, msgId, label));

      try {
        await sendCommandOverWs(
          {
            type: "command",
            command: "resolve_hitl",
            args: {
              message_id: msgId,
              action_label: label,
            },
            workspace_id: rlmApiConfig.workspaceId,
            user_id: rlmApiConfig.userId,
            session_id: sessionId,
          },
          {
            onFrame: (frame) => {
              setMessages((prev) => {
                const result = applyWsFrameToMessages(prev, frame, queryClient);
                return result.messages;
              });
              onFrame(frame);
            },
          },
        );
      } catch (error) {
        setMessages((prev) => revertOptimisticHitlResolution(prev, msgId));
        const message =
          error instanceof Error ? error.message : "Unknown command error";
        toast.error("Failed to resolve checkpoint", { description: message });
      }
    },
    [onFrame, queryClient, sessionId, setMessages],
  );

  const resolveClarification = useCallback(() => {
    toast("Live backend mode", {
      description:
        "Clarification cards are currently available when emitted by backend events.",
    });
  }, []);

  const loadConversation = useCallback(
    (conversation: Conversation) => {
      stopStreaming();
      clearArtifactSteps();
      setMessages(conversation.messages);
      setInputValue("");
      setPhase(conversation.phase);
      setCreationPhase(conversation.phase);
      setIsTyping(false);
    },
    [clearArtifactSteps, setCreationPhase, stopStreaming, setMessages],
  );

  return {
    messages,
    inputValue,
    setInputValue,
    phase,
    // combined streaming and typing status
    isTyping: isTyping || isStreaming,
    handleSubmit,
    resolveHitl,
    resolveClarification,
    loadConversation,
  };
}
