import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useTelemetry } from "@/lib/telemetry/use-telemetry";
import { useAppNavigate } from "@/hooks/use-app-navigate";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { useRuntimeStatus } from "@/hooks/use-runtime-status";
import { WorkspaceMessageList } from "@/features/workspace/conversation/transcript/workspace-message-list";
import {
  useChatHistoryStore,
  useChatStore,
  useWorkspace,
  useWorkspaceUiStore,
} from "@/features/workspace/use-workspace";
import { getWorkspaceRuntimeGuard } from "@/features/workspace/runtime-guard";
import { detectRepoContext } from "@/lib/utils/repo-context";
import { detectContextPaths } from "@/lib/utils/source-context";
import { isRlmCoreEnabled } from "@/lib/rlm-api";
import type { WsExecutionMode } from "@/lib/rlm-api/ws-types";
import { requestSettingsDialogOpen } from "@/features/settings/settings-events";

/**
 * Composer placeholder text based on current state.
 * Provides contextual guidance to help users understand what to do next.
 */
function getComposerPlaceholder(options: {
  backendEnabled: boolean;
  phase: string;
  hasMessages: boolean;
}): string {
  if (!options.backendEnabled) {
    return "Backend not configured — check Settings → Runtime";
  }

  if (options.phase === "idle") {
    return options.hasMessages
      ? "Continue the conversation or start a new task…"
      : "Describe what you'd like to build or accomplish…";
  }

  return "Ask a follow-up question or provide more context…";
}

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

  const didInitRuntimeMode = useRef(false);
  useEffect(() => {
    if (didInitRuntimeMode.current) return;
    didInitRuntimeMode.current = true;
    setRuntimeMode("daytona_pilot");
  }, [setRuntimeMode]);

  const handleOpenRuntimeSettings = useCallback(() => {
    const wasHandledByDialog = requestSettingsDialogOpen({
      section: "runtime",
    });
    if (!wasHandledByDialog) {
      navigate({ to: "/settings", search: { section: "runtime" } });
    }
  }, [navigate]);

  // Wrap handleSubmit to capture chat session start event on first message
  const handleSubmit = useCallback(
    (content: string) => {
      const runtimeGuard = getWorkspaceRuntimeGuard(runtimeStatus.data);
      if (backendEnabled && runtimeGuard.blocked) {
        if (content.trim()) {
          toast.error(runtimeGuard.title, {
            description:
              runtimeGuard.guidance[0] ??
              "Fix runtime credentials or connectivity before starting a Workbench run.",
          });
        }
        handleOpenRuntimeSettings();
        return;
      }

      const inferredRepoContext = detectRepoContext(content);
      const inferredContextPaths = detectContextPaths(content);

      if (phase === "idle" && messages.length === 0 && content.trim()) {
        telemetry.capture("chat_session_started", {
          prompt_length: content.length,
        });
      }
      originalHandleSubmit({
        text: content,
        executionMode,
        runtimeMode,
        repoUrl: inferredRepoContext?.repoUrl,
        repoRef: inferredRepoContext?.repoRefCandidate ?? inferredRepoContext?.repoRef,
        contextPaths: inferredContextPaths.length > 0 ? inferredContextPaths : undefined,
      });
    },
    [
      phase,
      messages.length,
      telemetry,
      originalHandleSubmit,
      executionMode,
      runtimeMode,
      runtimeStatus.data,
      backendEnabled,
      handleOpenRuntimeSettings,
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

  const runtimeGuard = getWorkspaceRuntimeGuard(runtimeStatus.data);
  const warningGuidance = runtimeGuard.guidance;
  const showRuntimeWarning = backendEnabled && runtimeGuard.showWarning;
  const runtimeWarningTitle = runtimeGuard.title;
  const hasMessages = messages.length > 0;
  const composerCanSubmit = backendEnabled && !isTyping;
  const isReceivingResponse = backendEnabled && isTyping;

  const composerPlaceholder = getComposerPlaceholder({
    backendEnabled,
    phase,
    hasMessages,
  });

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      <div className="flex flex-row flex-1 min-h-0 overflow-hidden">
        {/* Main chat column */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <div className="flex-1 min-h-0" data-slot="workspace-agent-chat">
            <WorkspaceMessageList
              messages={messages}
              isTyping={isReceivingResponse}
              isMobile={isMobile}
              showEmptyState={!hasMessages && phase === "idle" && !isTyping}
              onSuggestionClick={setInputValue}
              onResolveHitl={resolveHitl}
              onResolveClarification={resolveClarification}
              value={inputValue}
              onChange={setInputValue}
              onSend={handleSubmit}
              onStop={stopStreaming}
              executionMode={executionMode}
              onExecutionModeChange={setExecutionMode}
              canSubmit={composerCanSubmit}
              placeholder={composerPlaceholder}
              runtimeWarning={
                showRuntimeWarning
                  ? {
                      title: runtimeWarningTitle,
                      description: runtimeGuard.description,
                      guidance: warningGuidance,
                      onOpenSettings: handleOpenRuntimeSettings,
                    }
                  : undefined
              }
              activeModels={runtimeStatus.data?.active_models}
              onOpenModelSettings={handleOpenRuntimeSettings}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
