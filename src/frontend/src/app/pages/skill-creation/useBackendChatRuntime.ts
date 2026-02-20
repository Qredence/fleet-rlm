import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  createBackendSessionId,
  rlmApiConfig,
  streamChatOverWs,
  type WsMessageRequest,
  type WsServerMessage,
} from "../../lib/rlm-api";
import { useNavigation } from "../../components/hooks/useNavigation";
import type { ChatMessage, CreationPhase } from "../../components/data/types";
import type { Conversation } from "../../components/hooks/useChatHistory";
import { applyWsFrameToMessages } from "./backendChatEventAdapter";
import { applyWsFrameToArtifacts } from "./backendArtifactEventAdapter";
import type { ChatSimulation } from "./useChatSimulation";
import { useArtifactStore } from "../../stores/artifactStore";

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
  const { setCreationPhase, sessionId, isCanvasOpen, openCanvas } =
    useNavigation();
  const clearArtifactSteps = useArtifactStore((state) => state.clear);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [phase, setPhase] = useState<CreationPhase>("idle");
  const [isTyping, setIsTyping] = useState(false);

  const isFirstMount = useRef(true);
  const streamControllerRef = useRef<AbortController | null>(null);
  const streamInFlightRef = useRef(false);
  const backendSessionIdRef = useRef<string>(createBackendSessionId());

  const stopStreaming = useCallback(() => {
    streamInFlightRef.current = false;
    streamControllerRef.current?.abort();
    streamControllerRef.current = null;
    setIsTyping(false);
  }, []);

  const resetRuntime = useCallback(() => {
    stopStreaming();
    backendSessionIdRef.current = createBackendSessionId();
    setMessages([]);
    setInputValue("");
    setPhase("idle");
    setCreationPhase("idle");
    clearArtifactSteps();
  }, [clearArtifactSteps, setCreationPhase, stopStreaming]);

  useEffect(() => {
    if (isFirstMount.current) {
      isFirstMount.current = false;
      return;
    }

    resetRuntime();
  }, [sessionId, resetRuntime]);

  const onFrame = useCallback(
    (frame: WsServerMessage) => {
      // Any backend frame means the server started responding.
      setIsTyping(false);

      applyWsFrameToArtifacts(frame);
      setMessages((prev) => applyWsFrameToMessages(prev, frame).messages);

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
    if (!text || isTyping || streamInFlightRef.current) return;

    setInputValue("");
    setMessages((prev) => [...prev, toUserMessage(text)]);
    setPhase("understanding");
    setCreationPhase("understanding");
    setIsTyping(true);
    clearArtifactSteps();
    if (!isCanvasOpen) openCanvas();

    const controller = new AbortController();
    streamControllerRef.current = controller;
    streamInFlightRef.current = true;

    const payload: WsMessageRequest = {
      type: "message",
      content: text,
      trace: rlmApiConfig.trace,
      workspace_id: rlmApiConfig.workspaceId,
      user_id: rlmApiConfig.userId,
      session_id: backendSessionIdRef.current,
      trace_mode: "compact",
    };

    let terminalSeen = false;

    try {
      await streamChatOverWs(payload, {
        signal: controller.signal,
        onFrame: (frame) => {
          if (isTerminalFrame(frame)) terminalSeen = true;
          onFrame(frame);
        },
      });
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }

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
      streamInFlightRef.current = false;
      streamControllerRef.current = null;
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
    onFrame,
    openCanvas,
    setCreationPhase,
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
    },
    [clearArtifactSteps, setCreationPhase, stopStreaming],
  );

  return {
    messages,
    inputValue,
    setInputValue,
    phase,
    isTyping,
    handleSubmit,
    resolveHitl,
    resolveClarification,
    loadConversation,
  };
}
