import { useEffect, useMemo, type ReactNode } from "react";
import type { ChatStatus } from "ai";

import { AgentChat } from "@/components/agent-elements/agent-chat";
import type { InputBarProps } from "@/components/agent-elements/input-bar";
import { WorkspaceAgentInputBar } from "@/features/workspace/conversation/workspace-agent-input-bar";
import { toAgentChatMessages } from "@/features/workspace/conversation/agent-chat-adapter";
import { buildPendingAssistantTurnId } from "@/lib/workspace/chat-display-items";
import { WorkspaceChatEmptyStateHero } from "@/features/workspace/conversation/transcript/workspace-chat-empty-state";
import { workspaceChatSuggestionItems } from "@/features/workspace/conversation/transcript/workspace-chat-suggestions";
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
  activeModels?: {
    planner?: string | null;
    delegate?: string | null;
    delegate_small?: string | null;
  };
  onOpenModelSettings?: () => void;
  rightActions?: ReactNode;
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
  activeModels,
  onOpenModelSettings,
  rightActions,
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
            activeModels={activeModels}
            onOpenModelSettings={onOpenModelSettings}
            rightActions={rightActions}
            showStatusBar={showStatusBar}
            runtimeWarning={runtimeWarning}
          />
        );
      },
    [
      activeModels,
      canSubmit,
      executionMode,
      onExecutionModeChange,
      onOpenModelSettings,
      placeholder,
      rightActions,
      runtimeWarning,
      showStatusBar,
    ],
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

  const emptyChatSuggestions = useMemo(
    () => ({
      items: workspaceChatSuggestionItems(),
      onSelect: (item: { value?: string; label: string }) =>
        onSuggestionClick(item.value ?? item.label),
      className: "w-full justify-center",
    }),
    [onSuggestionClick],
  );

  const sharedAgentChatProps = {
    messages: agentMessages,
    status,
    onSend: (message: { content: string }) => onSend(message.content),
    onStop: onStop ?? (() => {}),
    value,
    onChange,
    slots: { InputBar: inputSlot },
    showCopyToolbar: false as const,
    enableImagePreview: false as const,
    questionTool: {
      submitLabel: "Send",
      skipLabel: "Skip",
      allowSkip: false,
      onAnswer: handleQuestionAnswer,
    },
  };

  if (messages.length === 0 && showEmptyState) {
    return (
      <AgentChat
        {...sharedAgentChatProps}
        emptyStatePosition="center"
        emptySuggestionsPlacement="empty"
        emptySuggestionsPosition="top"
        emptyState={<WorkspaceChatEmptyStateHero isMobile={isMobile} />}
        suggestions={emptyChatSuggestions}
        className={cn("h-full min-h-0 bg-background", className)}
        classNames={{ inputBar: "px-4 pb-5 md:px-6" }}
      />
    );
  }

  return (
    <AgentChat
      {...sharedAgentChatProps}
      showCopyToolbar
      className={cn("bg-background", className)}
      classNames={{
        root: "bg-background",
        inputBar: "px-4 pb-5 pt-3 md:px-6",
        userMessage: "mx-auto max-w-an",
      }}
    />
  );
}
