import { useState, useCallback, useEffect, useRef } from "react";
import { TriangleAlert } from "lucide-react";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { useNavigationStore } from "@/stores/navigationStore";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useChatHistoryStore } from "@/stores/chatHistoryStore";
import { useChatStore } from "@/stores/chatStore";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/hooks/useIsMobile";
import { cn } from "@/lib/utils/cn";
import { Button } from "@/components/ui/button";
import { ChatInput, type AttachedFile } from "@/components/chat/ChatInput";
import { DaytonaSetupCard } from "@/features/rlm-workspace/DaytonaSetupCard";
import { ConversationHistory } from "@/features/rlm-workspace/ConversationHistory";
import { ChatMessageList } from "@/features/rlm-workspace/ChatMessageList";
import { useDaytonaWorkbenchStore } from "@/features/rlm-workspace/daytona-workbench/daytonaWorkbenchStore";
import {
  detectDaytonaRepoContext,
  normalizeDaytonaRepoUrl,
  resolveDaytonaRepoContext,
} from "@/features/rlm-workspace/daytonaRepoContext";
import { parseDaytonaContextPaths } from "@/features/rlm-workspace/daytonaSourceContext";
import { useBackendChatRuntime } from "@/features/rlm-workspace/useBackendChatRuntime";
import { useRuntimeStatus } from "@/features/settings/useRuntimeSettings";
import { isRlmCoreEnabled } from "@/lib/rlm-api";
import type { WsExecutionMode } from "@/lib/rlm-api/wsTypes";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

/**
 * RlmWorkspace — chat-first DSPy.RLM runtime surface.
 *
 * Chat logic (messages, phases, backend events) lives in `useBackendChatRuntime`.
 * Shared session state still flows through `NavigationStore` so it persists
 * across shell navigation.
 *
 * Conversation history is managed by `useChatHistoryStore` (localStorage-backed).
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
    turnArtifactsByMessageId,
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
  const runtimeMode = useChatStore((state) => state.runtimeMode);
  const setRuntimeMode = useChatStore((state) => state.setRuntimeMode);
  const daytonaRepoUrl = useChatStore((state) => state.daytonaRepoUrl);
  const setDaytonaRepoUrl = useChatStore((state) => state.setDaytonaRepoUrl);
  const daytonaRepoRef = useChatStore((state) => state.daytonaRepoRef);
  const setDaytonaRepoRef = useChatStore((state) => state.setDaytonaRepoRef);
  const daytonaContextPaths = useChatStore((state) => state.daytonaContextPaths);
  const setDaytonaContextPaths = useChatStore(
    (state) => state.setDaytonaContextPaths,
  );
  const daytonaMaxDepth = useChatStore((state) => state.daytonaMaxDepth);
  const setDaytonaMaxDepth = useChatStore((state) => state.setDaytonaMaxDepth);
  const daytonaBatchConcurrency = useChatStore(
    (state) => state.daytonaBatchConcurrency,
  );
  const setDaytonaBatchConcurrency = useChatStore(
    (state) => state.setDaytonaBatchConcurrency,
  );
  const activeDaytonaRunStatus = useDaytonaWorkbenchStore((state) => state.status);
  const activeDaytonaRunRepoUrl = useDaytonaWorkbenchStore(
    (state) => state.repoUrl,
  );
  const activeDaytonaRunContextSources = useDaytonaWorkbenchStore(
    (state) => state.contextSources,
  );
  const detectedDaytonaRepoContext =
    runtimeMode === "daytona_pilot"
      ? detectDaytonaRepoContext(inputValue)
      : null;
  const normalizedManualDaytonaRepoUrl =
    runtimeMode === "daytona_pilot"
      ? normalizeDaytonaRepoUrl(daytonaRepoUrl)
      : null;
  const hasManualDaytonaRepoOverride =
    runtimeMode === "daytona_pilot" && daytonaRepoUrl.trim().length > 0;
  const hasInvalidManualDaytonaRepoOverride =
    hasManualDaytonaRepoOverride && normalizedManualDaytonaRepoUrl == null;
  const resolvedDaytonaRepoContext =
    runtimeMode === "daytona_pilot"
      ? resolveDaytonaRepoContext({
          manualRepoUrl: daytonaRepoUrl,
          promptText: inputValue,
        })
      : null;
  const parsedDaytonaContextPaths =
    runtimeMode === "daytona_pilot"
      ? parseDaytonaContextPaths(daytonaContextPaths)
      : [];
  const canSubmitDaytonaRun =
    runtimeMode !== "daytona_pilot" || !hasInvalidManualDaytonaRepoOverride;
  const showActiveDaytonaRunContext =
    runtimeMode === "daytona_pilot" &&
    (activeDaytonaRunStatus === "bootstrapping" ||
      activeDaytonaRunStatus === "running" ||
      activeDaytonaRunStatus === "cancelling");

  // Wrap handleSubmit to capture chat session start event on first message
  const handleSubmit = useCallback(
    (attachments: AttachedFile[]) => {
      const effectiveDaytonaRepoUrl =
        runtimeMode === "daytona_pilot"
          ? resolvedDaytonaRepoContext?.repoUrl
          : undefined;
      if (phase === "idle" && messages.length === 0 && inputValue.trim()) {
        telemetry.capture("chat_session_started", {
          prompt_length: inputValue.length,
        });
      }
      originalHandleSubmit({
        executionMode: runtimeMode === "modal_chat" ? executionMode : undefined,
        runtimeMode,
        repoUrl: effectiveDaytonaRepoUrl,
        repoRef:
          runtimeMode === "daytona_pilot" && effectiveDaytonaRepoUrl
            ? daytonaRepoRef
            : undefined,
        contextPaths:
          runtimeMode === "daytona_pilot"
            ? parsedDaytonaContextPaths
            : undefined,
        maxDepth: runtimeMode === "daytona_pilot" ? daytonaMaxDepth : undefined,
        batchConcurrency:
          runtimeMode === "daytona_pilot"
            ? daytonaBatchConcurrency
            : undefined,
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
      runtimeMode,
      resolvedDaytonaRepoContext,
      daytonaRepoRef,
      parsedDaytonaContextPaths,
      daytonaMaxDepth,
      daytonaBatchConcurrency,
    ],
  );

  const { sessionId } = useNavigationStore();

  // Chat history
  const {
    conversations,
    saveConversation,
    loadConversation: loadConv,
    deleteConversation,
    clearHistory,
  } = useChatHistoryStore();

  // ── History panel toggle ─────────────────────────────────────────
  const [showHistory, setShowHistory] = useState(false);

  // ── Auto-save on session change ──────────────────────────────────
  // When sessionId increments (newSession() called), save the current
  // conversation before the backend runtime resets local chat state.
  const prevSessionIdRef = useRef(sessionId);
  const messagesRef = useRef(messages);
  const turnArtifactsRef = useRef(turnArtifactsByMessageId);
  const phaseRef = useRef(phase);

  useEffect(() => {
    messagesRef.current = messages;
    turnArtifactsRef.current = turnArtifactsByMessageId;
    phaseRef.current = phase;
  }, [messages, phase, turnArtifactsByMessageId]);

  useEffect(() => {
    let historyResetTimer: ReturnType<typeof setTimeout> | null = null;

    if (prevSessionIdRef.current !== sessionId) {
      // Save the old conversation (if it had messages)
      if (messagesRef.current.length > 0) {
        saveConversation(
          messagesRef.current,
          phaseRef.current,
          undefined,
          turnArtifactsRef.current,
        );
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
        saveConversation(messages, phase, undefined, turnArtifactsByMessageId);
      }
      loadConversation(conv);
      setShowHistory(false);
    },
    [
      loadConv,
      loadConversation,
      messages,
      phase,
      saveConversation,
      turnArtifactsByMessageId,
    ],
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
  const hasMessages = messages.length > 0;
  const composerDisabled = isTyping || !backendEnabled;
  const isReceivingResponse = backendEnabled && isTyping;
  const showDaytonaSetup = runtimeMode === "daytona_pilot";

  const composer = (
    <ChatInput
      value={inputValue}
      onChange={setInputValue}
      onSend={handleSubmit}
      attachmentsEnabled={false}
      placeholder={
        !backendEnabled
          ? "Configure FastAPI backend to start chatting…"
          : phase === "idle"
            ? "Ask anything…"
            : "Ask a follow-up…"
      }
      isLoading={composerDisabled}
      isReceiving={isReceivingResponse}
      runtimeMode={runtimeMode}
      onRuntimeModeChange={setRuntimeMode}
      executionMode={executionMode}
      onExecutionModeChange={setExecutionMode}
      canSubmit={canSubmitDaytonaRun}
      className="w-full"
    />
  );

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {/* Messages */}
      <div className="flex-1 min-h-0">
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
      </div>

      {/* Input composer */}
      <div
        className={cn(
          "sticky bottom-0 z-10 shrink-0 bg-linear-to-t from-background via-background to-transparent px-4 pb-6 md:px-6",
          hasMessages || showRuntimeWarning || showDaytonaSetup ? "pt-5" : "pt-2",
        )}
      >
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
            {showDaytonaSetup ? (
              <div className="mx-auto w-full max-w-175">
                <DaytonaSetupCard
                  manualRepoUrl={daytonaRepoUrl}
                  onManualRepoUrlChange={setDaytonaRepoUrl}
                  contextPaths={daytonaContextPaths}
                  onContextPathsChange={setDaytonaContextPaths}
                  repoRef={daytonaRepoRef}
                  onRepoRefChange={setDaytonaRepoRef}
                  maxDepth={daytonaMaxDepth}
                  onMaxDepthChange={setDaytonaMaxDepth}
                  batchConcurrency={daytonaBatchConcurrency}
                  onBatchConcurrencyChange={setDaytonaBatchConcurrency}
                  detectedRepoContext={detectedDaytonaRepoContext}
                  resolvedRepoContext={resolvedDaytonaRepoContext}
                  hasInvalidManualOverride={hasInvalidManualDaytonaRepoOverride}
                  activeRunRepoUrl={activeDaytonaRunRepoUrl}
                  activeRunContextSources={activeDaytonaRunContextSources}
                  isActiveRunContextVisible={showActiveDaytonaRunContext}
                />
              </div>
            ) : null}
            <div className="mx-auto w-full max-w-175">{composer}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
