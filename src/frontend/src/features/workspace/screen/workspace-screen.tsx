import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";

import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { useTelemetry } from "@/lib/telemetry/use-telemetry";
import { useAppNavigate } from "@/hooks/use-app-navigate";
import { useIsMobile } from "@/hooks/ui/use-is-mobile";
import { useRuntimeStatus } from "@/hooks/runtime/use-runtime-status";
import { WorkspaceMessageList } from "@/features/workspace/conversation/transcript/workspace-message-list";
import {
  WorkspaceSidepanel,
  WorkspaceSidepanelToggle,
} from "@/features/workspace/sidepanel/workspace-sidepanel";
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
import { requestSettingsDialogOpen } from "@/features/settings";

export const WORKSPACE_CHAT_OPEN_SIZE = "68%";
export const WORKSPACE_CHAT_MIN_SIZE = "25%";
export const WORKSPACE_SIDEPANEL_OPEN_SIZE = "32%";
export const WORKSPACE_SIDEPANEL_MIN_SIZE = "24%";
export const WORKSPACE_SIDEPANEL_MAX_SIZE = "75%";

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
  const sessionId = useChatStore((state) => state.sessionId);
  const runtimeSessionId = useChatStore((state) => state.runtimeSessionId);
  const durableSessionId = useChatStore((state) => state.durableSessionId);
  const [executionMode, setExecutionMode] = useState<WsExecutionMode>("auto");
  const runtimeMode = useChatStore((state) => state.runtimeMode);
  const setRuntimeMode = useChatStore((state) => state.setRuntimeMode);
  const [headerActionsHost, setHeaderActionsHost] = useState<HTMLElement | null>(null);

  const didInitRuntimeMode = useRef(false);
  useEffect(() => {
    if (didInitRuntimeMode.current) return;
    didInitRuntimeMode.current = true;
    setRuntimeMode("daytona_pilot");
  }, [setRuntimeMode]);

  useEffect(() => {
    const updateHeaderActionsHost = () => {
      setHeaderActionsHost(document.getElementById("workspace-header-actions"));
    };

    updateHeaderActionsHost();
    window.requestAnimationFrame(updateHeaderActionsHost);
  }, []);

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

  const getArtifactsHash = useCallback((artifacts: Record<string, unknown>) => {
    return Object.entries(artifacts)
      .map(([msgId, steps]) => `${msgId}:${Array.isArray(steps) ? steps.length : 0}`)
      .join(",");
  }, []);

  const lastSavedStateRef = useRef<{
    sessionId: string | null;
    messageCount: number;
    lastMessageId?: string;
    lastMessageContent?: string;
    phase: string;
    artifactsHash?: string;
  } | null>(null);

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

  // ── Auto-save on first message sent or turn completion ───────────
  useEffect(() => {
    if (messages.length === 0) return;

    // Condition 1: First user message was just sent (length is exactly 1)
    // Condition 2: Streaming or execution has finished (isTyping transitions to false)
    const isFirstMessage = messages.length === 1;
    const isTurnFinished = !isTyping;

    if (isFirstMessage || isTurnFinished) {
      const currentArtifactsHash = getArtifactsHash(turnArtifactsByMessageId);
      const lastMessage = messages[messages.length - 1];

      const isNewState =
        !lastSavedStateRef.current ||
        lastSavedStateRef.current.sessionId !== sessionId ||
        lastSavedStateRef.current.messageCount !== messages.length ||
        lastSavedStateRef.current.lastMessageId !== lastMessage?.id ||
        lastSavedStateRef.current.lastMessageContent !== lastMessage?.content ||
        lastSavedStateRef.current.phase !== phase ||
        lastSavedStateRef.current.artifactsHash !== currentArtifactsHash;

      if (isNewState) {
        saveConversation(messages, phase, undefined, turnArtifactsByMessageId, {
          runtimeSessionId,
          durableSessionId,
        });
        lastSavedStateRef.current = {
          sessionId,
          messageCount: messages.length,
          lastMessageId: lastMessage?.id,
          lastMessageContent: lastMessage?.content,
          phase,
          artifactsHash: currentArtifactsHash,
        };
      }
    }
  }, [
    messages,
    isTyping,
    phase,
    turnArtifactsByMessageId,
    saveConversation,
    sessionId,
    runtimeSessionId,
    durableSessionId,
    getArtifactsHash,
  ]);

  useEffect(() => {
    if (prevSessionRevisionRef.current !== sessionRevision) {
      // Save the old conversation (if it had messages)
      if (messagesRef.current.length > 0) {
        saveConversation(
          messagesRef.current,
          phaseRef.current,
          undefined,
          turnArtifactsRef.current,
          {
            runtimeSessionId,
            durableSessionId,
          },
        );
        // PostHog: Track conversation saved
        telemetry.capture("conversation_saved", {
          message_count: messagesRef.current.length,
        });
      }
      prevSessionRevisionRef.current = sessionRevision;
    }
  }, [sessionRevision, saveConversation, telemetry, runtimeSessionId, durableSessionId]);

  useEffect(() => {
    if (!requestedConversationId) return;

    const conversation = loadConv(requestedConversationId);
    if (!conversation) {
      clearRequestedConversation();
      return;
    }

    if (messagesRef.current.length > 0 && messagesRef.current !== conversation.messages) {
      saveConversation(messagesRef.current, phaseRef.current, undefined, turnArtifactsRef.current, {
        runtimeSessionId,
        durableSessionId,
      });
    }

    // Initialize the last saved state ref with the loaded conversation to prevent redundant save on load
    lastSavedStateRef.current = {
      sessionId,
      messageCount: conversation.messages.length,
      lastMessageId: conversation.messages[conversation.messages.length - 1]?.id,
      lastMessageContent: conversation.messages[conversation.messages.length - 1]?.content,
      phase: conversation.phase || "idle",
      artifactsHash: getArtifactsHash(conversation.turnArtifactsByMessageId || {}),
    };

    loadConversation(conversation);
    clearRequestedConversation();
  }, [
    clearRequestedConversation,
    loadConv,
    loadConversation,
    requestedConversationId,
    saveConversation,
    sessionId,
    runtimeSessionId,
    durableSessionId,
    getArtifactsHash,
  ]);

  const runtimeGuard = getWorkspaceRuntimeGuard(runtimeStatus.data);
  const warningGuidance = runtimeGuard.guidance;
  const showRuntimeWarning = backendEnabled && runtimeGuard.showWarning;
  const runtimeWarningTitle = runtimeGuard.title;
  const hasMessages = messages.length > 0;
  const composerCanSubmit = backendEnabled && !isTyping;
  const isReceivingResponse = backendEnabled && isTyping;
  const sidepanelOpen = useWorkspaceUiStore((state) => state.sidebarOpen);

  const composerPlaceholder = getComposerPlaceholder({
    backendEnabled,
    phase,
    hasMessages,
  });

  const headerActions = <WorkspaceSidepanelToggle />;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      {headerActionsHost ? createPortal(headerActions, headerActionsHost) : null}
      <div className="flex min-h-0 flex-1 flex-row overflow-hidden">
        <ResizablePanelGroup
          orientation="horizontal"
          defaultLayout={
            sidepanelOpen ? { "workspace-chat": 68, "workspace-sidepanel": 32 } : undefined
          }
          className="min-h-0 flex-1"
        >
          <ResizablePanel
            id="workspace-chat"
            minSize={sidepanelOpen ? WORKSPACE_CHAT_MIN_SIZE : undefined}
            defaultSize={sidepanelOpen ? WORKSPACE_CHAT_OPEN_SIZE : "100%"}
            className="min-w-0"
          >
            <div className="flex h-full min-h-0 flex-col overflow-hidden">
              <div className="min-h-0 flex-1" data-slot="workspace-agent-chat">
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
          </ResizablePanel>

          {!isMobile && sidepanelOpen ? (
            <>
              <ResizableHandle className="relative bg-border-subtle/70 transition-colors hover:bg-border focus-visible:ring-1 focus-visible:ring-accent after:absolute after:inset-y-0 after:left-1/2 after:w-2 after:-translate-x-1/2" />
              <ResizablePanel
                id="workspace-sidepanel"
                defaultSize={WORKSPACE_SIDEPANEL_OPEN_SIZE}
                minSize={WORKSPACE_SIDEPANEL_MIN_SIZE}
                maxSize={WORKSPACE_SIDEPANEL_MAX_SIZE}
                className="min-w-0"
              >
                <WorkspaceSidepanel isMobile={false} />
              </ResizablePanel>
            </>
          ) : null}
        </ResizablePanelGroup>
      </div>
      {isMobile ? <WorkspaceSidepanel isMobile /> : null}
    </div>
  );
}
