import { type ReactNode, type RefObject, useEffect } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/prompt-kit/conversation";
import { Message, MessageContent } from "@/components/prompt-kit/message";
import { AssistantTurnContent } from "@/features/rlm-workspace/assistant-content/AssistantTurnContent";
import { buildAssistantContentModel } from "@/features/rlm-workspace/assistant-content/buildAssistantContentModel";
import { fadeUp, fadeUpReduced } from "@/features/rlm-workspace/animation-presets";
import {
  buildChatDisplayItems,
  buildPendingAssistantTurnId,
  type AssistantTurnDisplayItem,
} from "@/features/rlm-workspace/chatDisplayItems";
import { WorkspaceChatEmptyState } from "@/features/rlm-workspace/chat-shell/WorkspaceChatEmptyState";
import { WorkspaceChatMessageItem } from "@/features/rlm-workspace/chat-shell/WorkspaceChatMessageItem";
import {
  ChatMessageLoadingState,
  WorkspaceToolSessionMessage,
} from "@/features/rlm-workspace/chat-shell/tracePartRenderers";
import type { ChatMessage, InspectorTab } from "@/lib/data/types";
import { cn } from "@/lib/utils/cn";
import { useNavigationStore } from "@/stores/navigationStore";

interface ChatMessageListProps {
  messages: ChatMessage[];
  isTyping: boolean;
  isMobile: boolean;
  scrollRef: RefObject<HTMLDivElement | null>;
  contentRef: RefObject<HTMLDivElement | null>;
  isAtBottom?: boolean;
  scrollToBottom?: () => void;
  onSuggestionClick: (text: string) => void;
  onResolveHitl: (msgId: string, label: string) => void;
  onResolveClarification: (msgId: string, answer: string) => void;
  showHistory?: boolean;
  onToggleHistory?: () => void;
  hasHistory?: boolean;
  historyPanel?: ReactNode;
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

export function ChatMessageList({
  messages,
  isTyping,
  isMobile,
  scrollRef: _scrollRef,
  contentRef: _contentRef,
  isAtBottom: _isAtBottom,
  scrollToBottom: _scrollToBottom,
  onSuggestionClick,
  onResolveHitl,
  onResolveClarification,
  showHistory,
  onToggleHistory,
  hasHistory,
  historyPanel,
}: ChatMessageListProps) {
  const { selectedAssistantTurnId, selectInspectorTurn, isCanvasOpen } = useNavigationStore();
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
  const displayItems = buildChatDisplayItems(messages, {
    showPendingAssistantShell: isTyping,
  });
  const hasVisibleAssistantTurn = displayItems.some((item) => item.kind === "assistant_turn");
  const showTypingShimmer = isTyping && !hasStreamingAssistant && !hasVisibleAssistantTurn;

  useEffect(() => {
    if (!selectedAssistantTurnId || !lastUserMessageId) return;
    const pendingTurnId = buildPendingAssistantTurnId(lastUserMessageId);
    if (selectedAssistantTurnId !== pendingTurnId || !activeTurnAssistantMessageId) {
      return;
    }
    useNavigationStore.setState({
      selectedAssistantTurnId: activeTurnAssistantMessageId,
    });
  }, [activeTurnAssistantMessageId, lastUserMessageId, selectedAssistantTurnId]);

  return (
    <Conversation className={cn("bg-background", messages.length === 0 && "flex-none")}>
      <ConversationContent
        className={cn(
          "mx-auto w-full max-w-175",
          messages.length === 0 ? "gap-3 pb-0" : "min-h-full",
        )}
      >
        {messages.length === 0 ? (
          <motion.div {...preset}>
            <WorkspaceChatEmptyState
              isMobile={isMobile}
              onSuggestionClick={onSuggestionClick}
              showHistory={showHistory}
              onToggleHistory={onToggleHistory}
              hasHistory={hasHistory}
            />
          </motion.div>
        ) : null}

        {messages.length === 0 ? <AnimatePresence>{historyPanel}</AnimatePresence> : null}

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
