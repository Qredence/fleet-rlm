import { useEffect, useMemo } from "react";
import type { ChatStatus } from "ai";

import { AgentChat } from "@/components/agent-elements/agent-chat";
import type { InputBarProps } from "@/components/agent-elements/input-bar";
import { WorkspaceAgentInputBar } from "@/features/workspace/conversation/workspace-agent-input-bar";
import { toAgentChatMessages } from "@/features/workspace/conversation/agent-chat-adapter";
import { buildPendingAssistantTurnId } from "@/lib/workspace/chat-display-items";
import { WorkspaceChatEmptyState } from "@/features/workspace/conversation/transcript/workspace-chat-empty-state";
import type { ChatMessage } from "@/features/workspace/use-workspace";
import { useWorkspaceUiStore } from "@/features/workspace/use-workspace";
import type { WsExecutionMode } from "@/lib/rlm-api/ws-types";
import { cn } from "@/lib/utils";

interface WorkspaceMessageListProps {
  messages: ChatMessage[];
  isTyping: boolean;
  isMobile: boolean;
  showEmptyState?: boolean;
  onSuggestionClick: (text: string) => void;
  onResolveHitl: (msgId: string, label: string) => void;
  onResolveClarification: (msgId: string, answer: string) => void;
  value?: string;
  onChange?: (value: string) => void;
  onSend?: (content: string) => void;
  onStop?: () => void;
  executionMode?: WsExecutionMode;
  onExecutionModeChange?: (mode: WsExecutionMode) => void;
  canSubmit?: boolean;
  placeholder?: string;
  runtimeWarning?: {
    title: string;
    description: string;
    guidance: string[];
    onOpenSettings: () => void;
  };
  showStatusBar?: boolean;
  className?: string;
}

function chatStatus(isTyping: boolean): ChatStatus {
  return isTyping ? "streaming" : "ready";
}

export function WorkspaceMessageList({
  messages,
  isTyping,
  isMobile,
  showEmptyState = true,
  onSuggestionClick,
  onResolveHitl,
  onResolveClarification,
  value = "",
  onChange = () => {},
  onSend = () => {},
  onStop,
  executionMode = "auto",
  onExecutionModeChange = () => {},
  canSubmit = true,
  placeholder,
  runtimeWarning,
  showStatusBar = true,
  className,
}: WorkspaceMessageListProps) {
  const selectedAssistantTurnId = useWorkspaceUiStore((state) => state.selectedAssistantTurnId);
  const agentMessages = useMemo(
    () =>
      toAgentChatMessages(messages, {
        onResolveHitl,
        onResolveClarification,
      }),
    [messages, onResolveClarification, onResolveHitl],
  );
  const lastUserIndex = messages.findLastIndex((message) => message.type === "user");
  const lastUserMessageId = lastUserIndex >= 0 ? (messages[lastUserIndex]?.id ?? null) : null;
  const activeTurnAssistantMessageId =
    lastUserIndex >= 0
      ? (messages
          .slice(lastUserIndex + 1)
          .reverse()
          .find((message) => message.type === "assistant")?.id ?? null)
      : null;

  useEffect(() => {
    if (!selectedAssistantTurnId || !lastUserMessageId) return;
    const pendingTurnId = buildPendingAssistantTurnId(lastUserMessageId);
    if (selectedAssistantTurnId !== pendingTurnId || !activeTurnAssistantMessageId) return;
    useWorkspaceUiStore.setState({
      selectedAssistantTurnId: activeTurnAssistantMessageId,
    });
  }, [activeTurnAssistantMessageId, lastUserMessageId, selectedAssistantTurnId]);

  const status = chatStatus(isTyping);
  const inputSlot = useMemo(
    () =>
      function WorkspaceInputSlot(props: InputBarProps) {
        return (
          <WorkspaceAgentInputBar
            {...props}
            disabled={props.disabled || !canSubmit}
            placeholder={placeholder ?? props.placeholder}
            executionMode={executionMode}
            onExecutionModeChange={onExecutionModeChange}
            showStatusBar={showStatusBar}
            runtimeWarning={runtimeWarning}
          />
        );
      },
    [canSubmit, executionMode, onExecutionModeChange, placeholder, runtimeWarning, showStatusBar],
  );

  const handleQuestionAnswer = ({
    toolCallId,
    question,
    answer,
  }: {
    toolCallId?: string;
    question: { options?: { id: string; label: string }[] };
    answer: { selectedIds?: string[]; text?: string; kind: string };
  }) => {
    if (!toolCallId) return;
    const target = messages.find((message) => message.id === toolCallId);
    const selectedLabels = (answer.selectedIds ?? []).map(
      (id) => question.options?.find((option) => option.id === id)?.label ?? id,
    );
    const text = [selectedLabels.join(", "), answer.text].filter(Boolean).join(" - ");
    if (!text) return;
    if (target?.type === "hitl") {
      onResolveHitl(toolCallId, text);
      return;
    }
    if (target?.type === "clarification") {
      onResolveClarification(toolCallId, text);
    }
  };

  if (messages.length === 0 && showEmptyState) {
    return (
      <div className={cn("flex h-full min-h-0 flex-col bg-background", className)}>
        <div className="flex min-h-0 flex-1 items-center justify-center px-4 py-4">
          <WorkspaceChatEmptyState isMobile={isMobile} onSuggestionClick={onSuggestionClick} />
        </div>
        <AgentChat
          messages={agentMessages}
          status={status}
          onSend={(message) => onSend(message.content)}
          onStop={onStop ?? (() => {})}
          value={value}
          onChange={onChange}
          slots={{ InputBar: inputSlot }}
          className="h-auto shrink-0"
          classNames={{ inputBar: "px-4 pb-6 md:px-6" }}
          showCopyToolbar={false}
          enableImagePreview={false}
          questionTool={{
            submitLabel: "Send",
            skipLabel: "Skip",
            allowSkip: false,
            onAnswer: handleQuestionAnswer,
          }}
        />
      </div>
    );
  }

  return (
    <AgentChat
      messages={agentMessages}
      status={status}
      onSend={(message) => onSend(message.content)}
      onStop={onStop ?? (() => {})}
      value={value}
      onChange={onChange}
      slots={{ InputBar: inputSlot }}
      className={cn("bg-background", className)}
      classNames={{
        root: "bg-background",
        inputBar: "px-4 pb-6 pt-4 md:px-6",
        userMessage: "mx-auto max-w-175",
      }}
      showCopyToolbar
      enableImagePreview={false}
      questionTool={{
        submitLabel: "Send",
        skipLabel: "Skip",
        allowSkip: false,
        onAnswer: handleQuestionAnswer,
      }}
    />
  );
}
