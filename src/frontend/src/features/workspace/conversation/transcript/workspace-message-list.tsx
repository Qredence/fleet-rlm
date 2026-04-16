import { useEffect, useMemo } from "react";
import { motion, useReducedMotion } from "motion/react";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { AssistantTurnContent } from "@/features/workspace/conversation/assistant-content/assistant-turn-content";
import { buildAssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import { fadeUp, fadeUpReduced } from "@/features/workspace/conversation/animation-presets";
import {
  buildChatDisplayItems,
  buildPendingAssistantTurnId,
  type AssistantTurnDisplayItem,
} from "@/lib/workspace/chat-display-items";
import { WorkspaceChatEmptyState } from "@/features/workspace/conversation/transcript/workspace-chat-empty-state";
import { WorkspaceChatMessageItem } from "@/features/workspace/conversation/transcript/workspace-chat-message-item";
import {
  ChatMessageLoadingState,
  WorkspaceToolSessionMessage,
} from "@/features/workspace/conversation/transcript/trace-part-renderers";
import type { ChatMessage, InspectorTab } from "@/features/workspace/use-workspace";
import { cn } from "@/lib/utils";
import { useIsCanvasOpen } from "@/stores/navigation-store";
import { useWorkspaceUiStore } from "@/features/workspace/use-workspace";

interface WorkspaceMessageListProps {
  messages: ChatMessage[];
  isTyping: boolean;
  isMobile: boolean;
  showEmptyState?: boolean;
  onSuggestionClick: (text: string) => void;
  onResolveHitl: (msgId: string, label: string) => void;
  onResolveClarification: (msgId: string, answer: string) => void;
}

function renderAssistantTurn(
  item: AssistantTurnDisplayItem,
  options: {
    selected: boolean;
    onOpenTab: (tab: InspectorTab) => void;
  },
) {
  return (
    <AssistantTurnContent
      model={buildAssistantContentModel(item)}
      selected={options.selected}
      onOpenTab={options.onOpenTab}
    />
  );
}

export function WorkspaceMessageList({
  messages,
  isTyping,
  isMobile,
  showEmptyState = true,
  onSuggestionClick,
  onResolveHitl,
  onResolveClarification,
}: WorkspaceMessageListProps) {
  const isCanvasOpen = useIsCanvasOpen();
  const selectedAssistantTurnId = useWorkspaceUiStore((state) => state.selectedAssistantTurnId);
  const selectInspectorTurn = useWorkspaceUiStore((state) => state.selectInspectorTurn);
  const prefersReduced = useReducedMotion();
  const preset = prefersReduced ? fadeUpReduced : fadeUp;
  const hasStreamingAssistant = messages.some(
    (message) => message.type === "assistant" && message.streaming,
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
  const displayItems = useMemo(
    () =>
      buildChatDisplayItems(messages, {
        showPendingAssistantShell: isTyping,
      }),
    [messages, isTyping],
  );
  const hasVisibleAssistantTurn = displayItems.some((item) => item.kind === "assistant_turn");
  const showTypingShimmer = isTyping && !hasStreamingAssistant && !hasVisibleAssistantTurn;

  useEffect(() => {
    if (!selectedAssistantTurnId || !lastUserMessageId) return;
    const pendingTurnId = buildPendingAssistantTurnId(lastUserMessageId);
    if (selectedAssistantTurnId !== pendingTurnId || !activeTurnAssistantMessageId) {
      return;
    }
    useWorkspaceUiStore.setState({
      selectedAssistantTurnId: activeTurnAssistantMessageId,
    });
  }, [activeTurnAssistantMessageId, lastUserMessageId, selectedAssistantTurnId]);

  return (
    <Conversation className="bg-background">
      <ConversationContent
        className={cn(
          "mx-auto w-full max-w-175",
          messages.length === 0
            ? "flex min-h-full items-center justify-center gap-0 pb-0"
            : "min-h-full",
        )}
      >
        {messages.length === 0 && showEmptyState ? (
          <motion.div {...preset}>
            <WorkspaceChatEmptyState isMobile={isMobile} onSuggestionClick={onSuggestionClick} />
          </motion.div>
        ) : null}

        {messages.length > 0 ? (
          <div className="mt-auto flex flex-col gap-4">
            {displayItems.map((displayItem) => {
              if (displayItem.kind === "tool_session") {
                return (
                  <motion.div key={displayItem.key} {...preset}>
                    <WorkspaceToolSessionMessage item={displayItem} />
                  </motion.div>
                );
              }

              if (displayItem.kind === "assistant_turn") {
                const turnId = displayItem.turnId;
                return (
                  <motion.div key={displayItem.key} {...preset}>
                    {renderAssistantTurn(displayItem, {
                      selected: isCanvasOpen && selectedAssistantTurnId === turnId,
                      onOpenTab: (tab) => selectInspectorTurn(turnId, tab),
                    })}
                  </motion.div>
                );
              }

              const message =
                displayItem.kind === "trace_message"
                  ? {
                      ...displayItem.message,
                      renderParts: displayItem.renderParts,
                    }
                  : displayItem.message;

              return (
                <motion.div key={displayItem.key} {...preset}>
                  <WorkspaceChatMessageItem
                    message={message}
                    onResolveHitl={onResolveHitl}
                    onResolveClarification={onResolveClarification}
                  />
                </motion.div>
              );
            })}
          </div>
        ) : null}

        {showTypingShimmer ? (
          <Message from="assistant" className="pt-2">
            <MessageContent className="max-w-xl">
              <ChatMessageLoadingState />
            </MessageContent>
          </Message>
        ) : null}
      </ConversationContent>

      <ConversationScrollButton />
    </Conversation>
  );
}
