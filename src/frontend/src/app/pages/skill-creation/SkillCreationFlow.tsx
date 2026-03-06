import { useState, useCallback, useEffect, useRef } from "react";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { useNavigation } from "@/hooks/useNavigation";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useChatHistory } from "@/hooks/useChatHistory";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/hooks/useIsMobile";
import { Button } from "@/components/ui/button";
import { ChatInput, type AttachedFile } from "@/components/chat/ChatInput";
import { ConversationHistory } from "@/screens/chat/ConversationHistory";
import { ChatMessageList } from "@/app/pages/skill-creation/ChatMessageList";
import { useBackendChatRuntime } from "@/app/pages/skill-creation/useBackendChatRuntime";
import { useRuntimeStatus } from "@/features/settings/useRuntimeSettings";
import { isRlmCoreEnabled } from "@/lib/rlm-api";

/**
 * SkillCreationFlow — chat-based skill creation orchestrator.
 *
 * Chat logic (messages, phases, backend events) lives in `useBackendChatRuntime`.
 * Prompt feature state (activeFeatures, mode, selectedSkills) lives in
 * `NavigationContext` so it persists across tab navigation.
 *
 * Conversation history is managed by `useChatHistory` (localStorage-backed).
 * Auto-saves the current conversation when `sessionId` changes (new session),
 * and allows loading past conversations from the welcome state.
 */
export function SkillCreationFlow() {
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

  const [selectedAgent, setSelectedAgent] = useState("auto");
  const [thinkEnabled, setThinkEnabled] = useState(false);

  // Wrap handleSubmit to capture chat session start event on first message
  const handleSubmit = useCallback(
    (attachments: AttachedFile[]) => {
      if (phase === "idle" && messages.length === 0 && inputValue.trim()) {
        telemetry.capture("chat_session_started", {
          prompt_length: inputValue.length,
        });
      }
      originalHandleSubmit({
        traceEnabled: thinkEnabled,
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
      thinkEnabled,
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
              <div className="rounded-2xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-foreground">
                <div>
                  <span className="font-medium">Runtime warning:</span>{" "}
                  {runtimeGuidance[0]}
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3 rounded-lg border-amber-500/35 bg-background/70 text-foreground hover:bg-amber-500/15"
                  onClick={handleOpenRuntimeSettings}
                >
                  Open Runtime Settings
                </Button>
              </div>
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
              selectedAgent={selectedAgent}
              onAgentChange={setSelectedAgent}
              thinkEnabled={thinkEnabled}
              onThinkToggle={() => setThinkEnabled((prev) => !prev)}
              className="w-full rounded-3xl border border-border-strong overflow-hidden bg-elevated-primary px-2 py-1 [box-shadow:var(--shadow-200-stronger)]"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
