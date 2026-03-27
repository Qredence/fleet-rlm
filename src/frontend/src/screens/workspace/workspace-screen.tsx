import { useCallback, useEffect, useRef, useState } from "react";
import { TriangleAlert } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/hooks/useIsMobile";
import { useRuntimeStatus, runtimeStatusQueryKey } from "@/hooks/useRuntimeStatus";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { WorkspaceComposer, type AttachedFile } from "@/app/workspace/workspace-composer";
import { WorkspaceMessageList } from "@/app/workspace/workspace-message-list";
import {
  useChatHistoryStore,
  useChatStore,
  useWorkspace,
  useWorkspaceUiStore,
} from "@/screens/workspace/use-workspace";
import { detectRepoContext } from "@/lib/utils/repoContext";
import { detectContextPaths } from "@/lib/utils/sourceContext";
import { isRlmCoreEnabled } from "@/lib/rlm-api";
import { runtimeEndpoints } from "@/lib/rlm-api/runtime";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/wsTypes";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { requestSettingsDialogOpen } from "@/screens/settings/settings-events";

/**
 * WorkspaceScreen — chat-first DSPy.RLM runtime surface.
 *
 * Chat logic (messages, phases, backend events) lives in `useWorkspace`.
 * Workspace-only shell state flows through the workspace screen slice so it
 * persists across shell navigation without leaking back into root stores.
 *
 * Conversation history is managed by `useChatHistoryStore` (localStorage-backed).
 * Auto-saves the current conversation when the session revision changes (new session),
 * and allows loading past conversations from the shell sidebar.
 */
export function WorkspaceScreen() {
  const isMobile = useIsMobile();
  const { navigate } = useAppNavigate();
  const telemetry = useTelemetry();
  const backendEnabled = isRlmCoreEnabled();
  const runtimeStatus = useRuntimeStatus({ enabled: backendEnabled });

  const chatRuntime = useWorkspace();

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
  const stopStreaming = useChatStore((state) => state.stopStreaming);
  const [executionMode, setExecutionMode] = useState<WsExecutionMode>("auto");
  const runtimeMode = useChatStore((state) => state.runtimeMode);
  const setRuntimeMode = useChatStore((state) => state.setRuntimeMode);
  const queryClient = useQueryClient();

  // ── Initialise runtimeMode from backend setting on first load ────────────
  const didInitRuntimeMode = useRef(false);
  useEffect(() => {
    if (didInitRuntimeMode.current) return;
    const provider = runtimeStatus.data?.sandbox_provider;
    if (!provider) return;
    didInitRuntimeMode.current = true;
    const mapped: WsRuntimeMode = provider === "daytona" ? "daytona_pilot" : "modal_chat";
    setRuntimeMode(mapped);
  }, [runtimeStatus.data?.sandbox_provider, setRuntimeMode]);

  // ── Keep backend SANDBOX_PROVIDER in sync when user switches dropdown ────
  const handleRuntimeModeChange = useCallback(
    (mode: WsRuntimeMode) => {
      setRuntimeMode(mode);
      const sandboxProvider = mode === "daytona_pilot" ? "daytona" : "modal";
      runtimeEndpoints
        .patchSettings({ updates: { SANDBOX_PROVIDER: sandboxProvider } })
        .then(() => queryClient.invalidateQueries({ queryKey: runtimeStatusQueryKey }))
        .catch(() => {
          // silent — settings PATCH failures don't block the chat
        });
    },
    [setRuntimeMode, queryClient],
  );

  // Wrap handleSubmit to capture chat session start event on first message
  const handleSubmit = useCallback(
    (attachments: AttachedFile[]) => {
      const inferredRepoContext =
        runtimeMode === "daytona_pilot" ? detectRepoContext(inputValue) : null;
      const inferredContextPaths =
        runtimeMode === "daytona_pilot" ? detectContextPaths(inputValue) : [];

      if (phase === "idle" && messages.length === 0 && inputValue.trim()) {
        telemetry.capture("chat_session_started", {
          prompt_length: inputValue.length,
        });
      }
      originalHandleSubmit({
        executionMode: runtimeMode === "modal_chat" ? executionMode : undefined,
        runtimeMode,
        repoUrl: runtimeMode === "daytona_pilot" ? inferredRepoContext?.repoUrl : undefined,
        repoRef:
          runtimeMode === "daytona_pilot"
            ? (inferredRepoContext?.repoRefCandidate ?? inferredRepoContext?.repoRef)
            : undefined,
        contextPaths:
          runtimeMode === "daytona_pilot" && inferredContextPaths.length > 0
            ? inferredContextPaths
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
    ],
  );

  const { sessionRevision, requestedConversationId, clearRequestedConversation } =
    useWorkspaceUiStore();

  // Chat history
  const { saveConversation, loadConversation: loadConv } = useChatHistoryStore();

  // ── Auto-save on session change ──────────────────────────────────
  // When sessionRevision increments (newSession() called), save the current
  // conversation before the backend runtime resets local chat state.
  const prevSessionRevisionRef = useRef(sessionRevision);
  const messagesRef = useRef(messages);
  const turnArtifactsRef = useRef(turnArtifactsByMessageId);
  const phaseRef = useRef(phase);

  useEffect(() => {
    messagesRef.current = messages;
    turnArtifactsRef.current = turnArtifactsByMessageId;
    phaseRef.current = phase;
  }, [messages, phase, turnArtifactsByMessageId]);

  useEffect(() => {
    if (prevSessionRevisionRef.current !== sessionRevision) {
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
      prevSessionRevisionRef.current = sessionRevision;
    }
  }, [sessionRevision, saveConversation, telemetry]);

  useEffect(() => {
    if (!requestedConversationId) return;

    const conversation = loadConv(requestedConversationId);
    if (!conversation) {
      clearRequestedConversation();
      return;
    }

    if (messagesRef.current.length > 0 && messagesRef.current !== conversation.messages) {
      saveConversation(messagesRef.current, phaseRef.current, undefined, turnArtifactsRef.current);
    }

    loadConversation(conversation);
    clearRequestedConversation();
  }, [
    clearRequestedConversation,
    loadConv,
    loadConversation,
    requestedConversationId,
    saveConversation,
  ]);

  const handleOpenRuntimeSettings = useCallback(() => {
    const wasHandledByDialog = requestSettingsDialogOpen({
      section: "runtime",
    });
    if (!wasHandledByDialog) {
      navigate({ to: "/settings", search: { section: "runtime" } });
    }
  }, [navigate]);

  const runtimeGuidance = runtimeStatus.data?.guidance ?? [];
  const daytonaStatus = runtimeStatus.data?.daytona as
    | {
        configured?: boolean;
        guidance?: string[];
      }
    | undefined;
  const daytonaGuidance = Array.isArray(daytonaStatus?.guidance) ? daytonaStatus.guidance : [];
  const warningGuidance = runtimeMode === "daytona_pilot" ? daytonaGuidance : runtimeGuidance;
  const showRuntimeWarning =
    backendEnabled &&
    runtimeStatus.data != null &&
    (runtimeMode === "daytona_pilot"
      ? daytonaStatus?.configured === false && daytonaGuidance.length > 0
      : runtimeStatus.data.ready === false && runtimeGuidance.length > 0);
  const runtimeWarningTitle =
    runtimeMode === "daytona_pilot" ? "Daytona setup required" : "Runtime warning";
  const hasMessages = messages.length > 0;
  const composerDisabled = isTyping || !backendEnabled;
  const isReceivingResponse = backendEnabled && isTyping;

  const composer = (
    <WorkspaceComposer
      value={inputValue}
      onChange={setInputValue}
      onSend={handleSubmit}
      onStop={stopStreaming}
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
      onRuntimeModeChange={handleRuntimeModeChange}
      executionMode={executionMode}
      onExecutionModeChange={setExecutionMode}
      className="w-full"
    />
  );

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {/* Messages */}
      <div className="flex-1 min-h-0">
        <WorkspaceMessageList
          messages={messages}
          isTyping={isTyping}
          isMobile={isMobile}
          onSuggestionClick={setInputValue}
          onResolveHitl={resolveHitl}
          onResolveClarification={resolveClarification}
        />
      </div>

      {/* Input composer */}
      <div
        className={cn(
          "sticky bottom-0 z-0 shrink-0 bg-linear-to-t from-background via-background to-transparent px-4 pb-6 md:px-6",
          hasMessages || showRuntimeWarning ? "pt-5" : "pt-2",
        )}
      >
        <div className="mx-auto w-full max-w-200">
          <div className="flex flex-col gap-4">
            {showRuntimeWarning ? (
              <Alert className="border-accent/25 bg-accent/5 text-foreground">
                <TriangleAlert className="size-4" />
                <AlertTitle>{runtimeWarningTitle}</AlertTitle>
                <AlertDescription>
                  <div className="flex flex-col gap-3">
                    {warningGuidance.map((msg) => (
                      <p key={msg}>{msg}</p>
                    ))}
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
            <div className="mx-auto w-full max-w-175">{composer}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
