import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useNavigationStore } from "@/stores/navigationStore";
import type { Conversation } from "@/stores/chatHistoryStore";
import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import { applyWsFrameToMessages } from "@/features/rlm-workspace/backendChatEventAdapter";
import { applyWsFrameToArtifacts } from "@/features/rlm-workspace/backendArtifactEventAdapter";
import { buildChatDisplayItems } from "@/features/rlm-workspace/chatDisplayItems";
import { parseDaytonaContextPaths } from "@/features/rlm-workspace/daytonaSourceContext";
import type {
  ChatRuntime,
  ChatSubmitOptions,
} from "@/features/rlm-workspace/runtime-types";
import { useArtifactStore } from "@/stores/artifactStore";
import { useChatStore } from "@/stores/chatStore";
import { useDaytonaWorkbenchStore } from "@/features/rlm-workspace/daytona-workbench/daytonaWorkbenchStore";
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
  } = useNavigationStore();
  const clearArtifactSteps = useArtifactStore((state) => state.clear);

  const {
    messages,
    turnArtifactsByMessageId,
    isStreaming,
    sessionId,
    runtimeMode,
    daytonaRepoUrl,
    daytonaRepoRef,
    daytonaContextPaths,
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
  const resetDaytonaWorkbench = useDaytonaWorkbenchStore((state) => state.reset);

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
    resetDaytonaWorkbench();
  }, [
    clearArtifactSteps,
    clearTurnArtifacts,
    resetDaytonaWorkbench,
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

      useDaytonaWorkbenchStore.getState().applyFrame(frame);

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
      const resolvedRuntimeMode = options?.runtimeMode ?? runtimeMode;
      if (resolvedRuntimeMode === "daytona_pilot") {
        useDaytonaWorkbenchStore.getState().beginRun({
          task: text,
          repoUrl: options?.repoUrl ?? daytonaRepoUrl,
          repoRef: options?.repoRef ?? daytonaRepoRef,
          contextPaths:
            options?.contextPaths ?? parseDaytonaContextPaths(daytonaContextPaths),
        });
      }
      setPhase("understanding");
      setCreationPhase("understanding");
      setIsTyping(true);
      clearArtifactSteps();

      let terminalSeen = false;
      let receivedFrame = false;

      try {
        await streamMessage(
          text,
          (frame) => {
            receivedFrame = true;
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
            maxDepth: options?.maxDepth,
            batchConcurrency: options?.batchConcurrency,
          },
        );
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Unknown streaming error";
        if (!terminalSeen) {
          if (resolvedRuntimeMode === "daytona_pilot") {
            useDaytonaWorkbenchStore.getState().failRun(message);
          } else if (!receivedFrame) {
            resetDaytonaWorkbench();
          }
          applyWsFrameToArtifacts({ type: "error", message });
          if (resolvedRuntimeMode !== "daytona_pilot") {
            setMessages((prev) => [
              ...prev,
              {
                id: createLocalMessageId("sys"),
                type: "system",
                content: `Backend error: ${message}`,
                phase: 1,
              },
            ]);
          }
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
      isTyping,
      isStreaming,
      onFrame,
      queryClient,
      resetDaytonaWorkbench,
      streamMessage,
      runtimeMode,
      daytonaContextPaths,
      daytonaRepoRef,
      daytonaRepoUrl,
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
      setTurnArtifactsByMessageId(conversation.turnArtifactsByMessageId ?? {});
      setMessages(conversation.messages);
      setInputValue("");
      setPhase(conversation.phase);
      setCreationPhase(conversation.phase);
      setIsTyping(false);
      resetDaytonaWorkbench();
    },
    [
      clearArtifactSteps,
      resetDaytonaWorkbench,
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
    // combined streaming and typing status
    isTyping: isTyping || isStreaming,
    handleSubmit,
    resolveHitl,
    resolveClarification,
    loadConversation,
  };
}
