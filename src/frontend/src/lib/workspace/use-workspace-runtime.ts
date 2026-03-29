import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import {
  sendCommandOverWs,
  rlmApiConfig,
  subscribeToExecutionStream,
  type WsServerMessage,
} from "@/lib/rlm-api";
import { useArtifactStore } from "@/lib/workspace/artifact-store";
import { applyWsFrameToArtifacts } from "@/lib/workspace/backend-artifact-event-adapter";
import { applyWsFrameToMessages } from "@/lib/workspace/backend-chat-event-adapter";
import { useChatStore } from "@/lib/workspace/chat-store";
import { buildChatDisplayItems } from "@/lib/workspace/chat-display-items";
import { useRunWorkbenchStore } from "@/lib/workspace/run-workbench-store";
import { useWorkspaceUiStore } from "@/lib/workspace/workspace-ui-store";
import type {
  ChatMessage,
  ChatRuntime,
  ChatSubmitOptions,
  Conversation,
  CreationPhase,
} from "@/lib/workspace/workspace-types";

function isTerminalFrame(frame: WsServerMessage): boolean {
  if (frame.type === "error") return true;
  return (
    frame.data.kind === "final" || frame.data.kind === "cancelled" || frame.data.kind === "error"
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

function latestAssistantTurnId(messages: ChatMessage[]): string | null {
  const displayItems = buildChatDisplayItems(messages);
  for (let index = displayItems.length - 1; index >= 0; index -= 1) {
    const item = displayItems[index];
    if (item?.kind !== "assistant_turn" || !item.message) continue;
    return item.turnId;
  }
  return null;
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

function revertOptimisticHitlResolution(messages: ChatMessage[], msgId: string): ChatMessage[] {
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

export function useWorkspace(): ChatRuntime {
  const setCreationPhase = useWorkspaceUiStore((state) => state.setCreationPhase);
  const sessionRevision = useWorkspaceUiStore((state) => state.sessionRevision);
  const clearArtifactSteps = useArtifactStore((state) => state.clear);

  const {
    messages,
    turnArtifactsByMessageId,
    isStreaming,
    sessionId,
    streamMessage,
    stopStreaming,
    resetSession,
    setMessages,
    setTurnArtifactsByMessageId,
    snapshotTurnArtifacts,
    clearTurnArtifacts,
    addMessage,
  } = useChatStore();

  const queryClient = useQueryClient();

  const [inputValue, setInputValue] = useState("");
  const [phase, setPhase] = useState<CreationPhase>("idle");
  const [isTyping, setIsTyping] = useState(false);
  const resetRunWorkbench = useRunWorkbenchStore((state) => state.reset);

  const isFirstMount = useRef(true);

  const resetRuntime = useCallback(() => {
    stopStreaming();
    resetSession();
    setInputValue("");
    setPhase("idle");
    setCreationPhase("idle");
    setIsTyping(false);
    clearArtifactSteps();
    clearTurnArtifacts();
    resetRunWorkbench();
  }, [
    clearArtifactSteps,
    clearTurnArtifacts,
    resetRunWorkbench,
    resetSession,
    setCreationPhase,
    stopStreaming,
  ]);

  useEffect(() => {
    if (isFirstMount.current) {
      isFirstMount.current = false;
      return;
    }

    resetRuntime();
  }, [sessionRevision, resetRuntime]);

  useEffect(() => {
    if (!sessionId) return;

    const unsubscribe = subscribeToExecutionStream(sessionId, {
      onFrame: (frame) => {
        setIsTyping(false);
        useRunWorkbenchStore.getState().applyFrame(frame);
        applyWsFrameToArtifacts(frame);

        if (isTerminalFrame(frame)) {
          const turnId = latestAssistantTurnId(useChatStore.getState().messages);
          if (turnId) {
            snapshotTurnArtifacts(turnId, useArtifactStore.getState().steps);
          }
          if (isErrorFrame(frame)) {
            setPhase("idle");
            setCreationPhase("idle");
          } else {
            setPhase("complete");
            setCreationPhase("complete");
          }
        }
      },
    });

    return () => unsubscribe();
  }, [sessionId, setCreationPhase, snapshotTurnArtifacts]);

  const onFrame = useCallback(
    (frame: WsServerMessage) => {
      setIsTyping(false);

      if (isTerminalFrame(frame)) {
        useRunWorkbenchStore.getState().applyFrame(frame);
      }

      if (isTerminalFrame(frame)) {
        const turnId = latestAssistantTurnId(useChatStore.getState().messages);
        if (turnId) {
          snapshotTurnArtifacts(turnId, useArtifactStore.getState().steps);
        }
        if (isErrorFrame(frame)) {
          setPhase("idle");
          setCreationPhase("idle");
        } else {
          setPhase("complete");
          setCreationPhase("complete");
        }
      }
    },
    [setCreationPhase, snapshotTurnArtifacts],
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
      useRunWorkbenchStore.getState().beginRun({
        task: text,
        repoUrl: options?.repoUrl,
        repoRef: options?.repoRef,
        contextPaths: options?.contextPaths,
      });
      setPhase("understanding");
      setCreationPhase("understanding");
      setIsTyping(true);
      clearArtifactSteps();

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
            runtimeMode: options?.runtimeMode,
            repoUrl: options?.repoUrl,
            repoRef: options?.repoRef,
            contextPaths: options?.contextPaths,
            batchConcurrency: options?.batchConcurrency,
          },
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown streaming error";
        if (!terminalSeen) {
          useRunWorkbenchStore.getState().failRun(message);
          applyWsFrameToArtifacts({ type: "error", message });
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
      addMessage,
      clearArtifactSteps,
      inputValue,
      isStreaming,
      isTyping,
      onFrame,
      queryClient,
      setCreationPhase,
      streamMessage,
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
        const message = error instanceof Error ? error.message : "Unknown command error";
        toast.error("Failed to resolve checkpoint", { description: message });
      }
    },
    [onFrame, queryClient, sessionId, setMessages],
  );

  const resolveClarification = useCallback(() => {
    toast("Live backend mode", {
      description: "Clarification cards are currently available when emitted by backend events.",
    });
  }, []);

  const loadConversation = useCallback(
    (conversation: Conversation) => {
      stopStreaming();
      clearArtifactSteps();
      setTurnArtifactsByMessageId(conversation.turnArtifactsByMessageId ?? {});
      setMessages(conversation.messages);
      setInputValue("");
      setPhase(conversation.phase);
      setCreationPhase(conversation.phase);
      setIsTyping(false);
      resetRunWorkbench();
    },
    [
      clearArtifactSteps,
      resetRunWorkbench,
      setCreationPhase,
      setMessages,
      setTurnArtifactsByMessageId,
      stopStreaming,
    ],
  );

  return {
    messages,
    turnArtifactsByMessageId,
    inputValue,
    setInputValue,
    phase,
    isTyping: isTyping || isStreaming,
    handleSubmit,
    resolveHitl,
    resolveClarification,
    loadConversation,
  };
}
