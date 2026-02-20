import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useNavigation } from "@/hooks/useNavigation";
import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import type { Conversation } from "@/hooks/useChatHistory";
import { applyWsFrameToArtifacts } from "@/app/pages/skill-creation/backendArtifactEventAdapter";
import type { ChatSimulation } from "@/app/pages/skill-creation/useChatSimulation";
import { useArtifactStore } from "@/stores/artifactStore";
import { useChatStore } from "@/features/chat/stores/chatStore";
import type { WsServerMessage } from "@/lib/rlm-api";

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

export function useBackendChatRuntime(): ChatSimulation {
  const {
    setCreationPhase,
    sessionId: navSessionId,
    isCanvasOpen,
    openCanvas,
  } = useNavigation();
  const clearArtifactSteps = useArtifactStore((state) => state.clear);

  const {
    messages,
    isStreaming,
    streamMessage,
    stopStreaming,
    resetSession,
    setMessages,
    addMessage,
  } = useChatStore();

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

  const handleSubmit = useCallback(async () => {
    const text = inputValue.trim();
    if (!text || isTyping || isStreaming) return;

    setInputValue("");
    addMessage(toUserMessage(text));
    setPhase("understanding");
    setCreationPhase("understanding");
    setIsTyping(true);
    clearArtifactSteps();
    if (!isCanvasOpen) openCanvas();

    let terminalSeen = false;

    try {
      await streamMessage(text, (frame) => {
        if (isTerminalFrame(frame)) terminalSeen = true;
        onFrame(frame);
      });
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
  }, [
    clearArtifactSteps,
    inputValue,
    isCanvasOpen,
    isTyping,
    isStreaming,
    onFrame,
    openCanvas,
    setCreationPhase,
    addMessage,
    streamMessage,
    setMessages,
  ]);

  const resolveHitl = useCallback(() => {
    toast("Live backend mode", {
      description:
        "HITL checkpoints are currently driven by backend events only.",
    });
  }, []);

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
