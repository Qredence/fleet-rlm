import { useState, useCallback, useEffect, useRef } from "react";
import { TriangleAlert } from "lucide-react";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { useNavigation } from "@/hooks/useNavigation";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useChatHistory } from "@/hooks/useChatHistory";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/hooks/useIsMobile";
import { Button } from "@/components/ui/button";
import { ChatInput, type AttachedFile } from "@/components/chat/ChatInput";
import { ConversationHistory } from "@/features/rlm-workspace/ConversationHistory";
import { ChatMessageList } from "@/features/rlm-workspace/ChatMessageList";
import { useBackendChatRuntime } from "@/features/rlm-workspace/useBackendChatRuntime";
import { useRuntimeStatus } from "@/features/settings/useRuntimeSettings";
import { isRlmCoreEnabled } from "@/lib/rlm-api";
import type { WsExecutionMode } from "@/lib/rlm-api/wsTypes";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

/**
 * RlmWorkspace — chat-first DSPy.RLM runtime surface.
 *
 * Chat logic (messages, phases, backend events) lives in `useBackendChatRuntime`.
 * Shared session state still flows through `NavigationContext` so it persists
 * across shell navigation.
 *
 * Conversation history is managed by `useChatHistory` (localStorage-backed).
 * Auto-saves the current conversation when `sessionId` changes (new session),
 * and allows loading past conversations from the welcome state.
 */
export function RlmWorkspace() {
  const isMobile = useIsMobile();
  const { navigate } = useAppNavigate();
  const telemetry = useTelemetry();
  const { scrollRef, contentRef, isAtBottom, scrollToBottom } =
    useStickToBottom();
  const backendEnabled = isRlmCoreEnabled();
  const runtimeStatus = useRuntimeStatus({ enabled: backendEnabled });

  const backendRuntime = useBackendChatRuntime();
  const chatRuntime = backendRuntime;

  const {
    messages,
    inputValue,
    setInputValue,
    phase,
    isTyping,
    handleSubmit: originalHandleSubmit,
    resolveHitl,
    resolveClarification,
    loadConversation,
  } = chatRuntime;

  const [executionMode, setExecutionMode] = useState<WsExecutionMode>("auto");

  // Wrap handleSubmit to capture chat session start event on first message
  const handleSubmit = useCallback(
    (attachments: AttachedFile[]) => {
      if (phase === "idle" && messages.length === 0 && inputValue.trim()) {
        telemetry.capture("chat_session_started", {
          prompt_length: inputValue.length,
        });
      }
      originalHandleSubmit({
        executionMode,
        attachments: attachments.map((attachment) => ({
          id: attachment.id,
          name: attachment.file.name,
          mimeType: attachment.file.type,
          sizeBytes: attachment.file.size,
        })),
      });
    },
    [
      phase,
      messages.length,
      inputValue,
      telemetry,
      originalHandleSubmit,
      executionMode,
    ],
  );

  const { sessionId } = useNavigation();

  // Chat history
  const {
    conversations,
    saveConversation,
    loadConversation: loadConv,
    deleteConversation,
    clearHistory,
  } = useChatHistory();

  // ── History panel toggle ─────────────────────────────────────────
  const [showHistory, setShowHistory] = useState(false);

  // ── Auto-save on session change ──────────────────────────────────
  // When sessionId increments (newSession() called), save the current
  // conversation before the backend runtime resets local chat state.
  const prevSessionIdRef = useRef(sessionId);
  const messagesRef = useRef(messages);
  const phaseRef = useRef(phase);

  useEffect(() => {
    messagesRef.current = messages;
    phaseRef.current = phase;
  }, [messages, phase]);

  useEffect(() => {
    let historyResetTimer: ReturnType<typeof setTimeout> | null = null;

    if (prevSessionIdRef.current !== sessionId) {
      // Save the old conversation (if it had messages)
      if (messagesRef.current.length > 0) {
        saveConversation(messagesRef.current, phaseRef.current);
        // PostHog: Track conversation saved
        telemetry.capture("conversation_saved", {
          message_count: messagesRef.current.length,
        });
      }
      prevSessionIdRef.current = sessionId;
      historyResetTimer = setTimeout(() => setShowHistory(false), 0);
    }

    return () => {
      if (historyResetTimer) clearTimeout(historyResetTimer);
    };
  }, [sessionId, saveConversation, telemetry]);

  const handleSelectConversation = useCallback(
    (id: string) => {
      const conv = loadConv(id);
      if (!conv) return;
      // Save current conversation first if it has messages
      if (messages.length > 0) {
        saveConversation(messages, phase);
      }
      loadConversation(conv);
      setShowHistory(false);
    },
    [loadConv, loadConversation, messages, phase, saveConversation],
  );

  const handleToggleHistory = useCallback(() => {
    setShowHistory((prev) => !prev);
  }, []);

  const handleCloseHistory = useCallback(() => {
    setShowHistory(false);
  }, []);

  const handleOpenRuntimeSettings = useCallback(() => {
    const openSettingsEvent = new CustomEvent<{ section: "runtime" }>(
      "open-settings",
      {
        detail: { section: "runtime" },
        cancelable: true,
      },
    );

    const wasHandledByDialog =
      document.dispatchEvent(openSettingsEvent) === false;
    if (!wasHandledByDialog) {
      navigate("/settings?section=runtime");
    }
  }, [navigate]);

  const runtimeGuidance = runtimeStatus.data?.guidance ?? [];
  const showRuntimeWarning =
    backendEnabled &&
    runtimeStatus.data != null &&
    runtimeStatus.data.ready === false &&
    runtimeGuidance.length > 0;
  const composerDisabled = isTyping || !backendEnabled;
  const isReceivingResponse = backendEnabled && isTyping;

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {/* Messages */}
      <ChatMessageList
        messages={messages}
        isTyping={isTyping}
        isMobile={isMobile}
        scrollRef={scrollRef}
        contentRef={contentRef}
        isAtBottom={isAtBottom}
        scrollToBottom={scrollToBottom}
        onSuggestionClick={setInputValue}
        onResolveHitl={resolveHitl}
        onResolveClarification={resolveClarification}
        showHistory={showHistory}
        onToggleHistory={handleToggleHistory}
        hasHistory={conversations.length > 0}
        historyPanel={
          showHistory ? (
            <ConversationHistory
              conversations={conversations}
              onSelect={handleSelectConversation}
              onDelete={deleteConversation}
              onClearAll={clearHistory}
              onClose={handleCloseHistory}
            />
          ) : null
        }
      />

      {/* Input composer */}
      <div className="shrink-0 bg-linear-to-t from-background via-background to-transparent px-4 pb-6 pt-6 md:px-6">
        <div className="mx-auto w-full max-w-200">
          <div className="flex flex-col gap-4">
            {showRuntimeWarning ? (
              <Alert className="border-accent/25 bg-accent/5 text-foreground">
                <TriangleAlert className="size-4" />
                <AlertTitle>Runtime warning</AlertTitle>
                <AlertDescription>
                  <div className="space-y-3">
                    <p>{runtimeGuidance[0]}</p>
                    <Button
                      variant="outline"
                      size="sm"
                      className="rounded-lg"
                      onClick={handleOpenRuntimeSettings}
                    >
                      Open Runtime Settings
                    </Button>
                  </div>
                </AlertDescription>
              </Alert>
            ) : null}
            <ChatInput
              value={inputValue}
              onChange={setInputValue}
              onSend={handleSubmit}
              attachmentsEnabled={false}
              placeholder={
                !backendEnabled
                  ? "Configure FastAPI backend to start chatting\u2026"
                  : phase === "idle"
                    ? "Ask anything\u2026"
                    : "Ask a follow-up\u2026"
              }
              isLoading={composerDisabled}
              isReceiving={isReceivingResponse}
              executionMode={executionMode}
              onExecutionModeChange={setExecutionMode}
              className="mx-auto w-full max-w-175 rounded-3xl border border-border-strong overflow-hidden bg-elevated-primary px-2 py-1 [box-shadow:var(--shadow-200-stronger)]"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
