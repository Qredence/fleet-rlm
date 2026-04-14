import { useCallback, useEffect, useRef, useState } from "react";
import { Settings2, TriangleAlert } from "lucide-react";

import { useTelemetry } from "@/lib/telemetry/use-telemetry";
import { useAppNavigate } from "@/hooks/use-app-navigate";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { useRuntimeStatus } from "@/hooks/use-runtime-status";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { WorkspaceComposer, type AttachedFile } from "@/features/workspace/ui/workspace-composer";
import { WorkspaceChatEmptyState } from "@/features/workspace/ui/transcript/workspace-chat-empty-state";
import { WorkspaceMessageList } from "@/features/workspace/ui/transcript/workspace-message-list";
import {
  useChatHistoryStore,
  useChatStore,
  useWorkspace,
  useWorkspaceUiStore,
} from "@/features/workspace/use-workspace";
import { detectRepoContext } from "@/lib/utils/repo-context";
import { detectContextPaths } from "@/lib/utils/source-context";
import { isRlmCoreEnabled } from "@/lib/rlm-api";
import type { WsExecutionMode } from "@/lib/rlm-api/ws-types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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

  // Wrap handleSubmit to capture chat session start event on first message
  const handleSubmit = useCallback(
    (attachments: AttachedFile[]) => {
      const inferredRepoContext = detectRepoContext(inputValue);
      const inferredContextPaths = detectContextPaths(inputValue);

      if (phase === "idle" && messages.length === 0 && inputValue.trim()) {
        telemetry.capture("chat_session_started", {
          prompt_length: inputValue.length,
        });
      }
      originalHandleSubmit({
        executionMode,
        runtimeMode,
        repoUrl: inferredRepoContext?.repoUrl,
        repoRef: inferredRepoContext?.repoRefCandidate ?? inferredRepoContext?.repoRef,
        contextPaths: inferredContextPaths.length > 0 ? inferredContextPaths : undefined,
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
  const warningGuidance = daytonaGuidance.length > 0 ? daytonaGuidance : runtimeGuidance;
  const showRuntimeWarning =
    backendEnabled &&
    runtimeStatus.data != null &&
    daytonaStatus?.configured === false &&
    warningGuidance.length > 0;
  const runtimeWarningTitle = "Sandbox configuration needed";
  const hasMessages = messages.length > 0;
  const showDesktopLandingState = !isMobile && !hasMessages && phase === "idle" && !isTyping;
  const composerDisabled = isTyping || !backendEnabled;
  const isReceivingResponse = backendEnabled && isTyping;

  const composerPlaceholder = getComposerPlaceholder({
    backendEnabled,
    phase,
    hasMessages,
  });

  const composer = (
    <WorkspaceComposer
      value={inputValue}
      onChange={setInputValue}
      onSend={handleSubmit}
      onStop={stopStreaming}
      attachmentsEnabled={false}
      placeholder={composerPlaceholder}
      isLoading={composerDisabled}
      isReceiving={isReceivingResponse}
      executionMode={executionMode}
      onExecutionModeChange={setExecutionMode}
      className="w-full"
    />
  );

  return (
    <div className="flex flex-col h-full w-full bg-background overflow-hidden">
      {showDesktopLandingState ? (
        <div className="flex min-h-0 flex-1 items-start justify-center px-6 pt-16 pb-8 lg:pt-24">
          <div
            data-slot="workspace-landing-state"
            className="mx-auto flex w-full max-w-[760px] flex-col items-center gap-5"
          >
            <WorkspaceChatEmptyState isMobile={false} onSuggestionClick={setInputValue} />

            {showRuntimeWarning ? (
              <Alert className="w-full border-amber-500/25 bg-amber-500/5 text-foreground rounded-xl">
                <TriangleAlert className="text-amber-500" />
                <AlertTitle className="text-sm font-medium">{runtimeWarningTitle}</AlertTitle>
                <AlertDescription>
                  <div className="flex flex-col gap-3 mt-1.5">
                    <p className="text-sm text-muted-foreground leading-relaxed">
                      Connect to a Daytona sandbox to enable secure code execution. Your code runs
                      in an isolated environment with persistent storage.
                    </p>
                    {warningGuidance.length > 0 && (
                      <ul className="text-xs text-muted-foreground/80 space-y-1 pl-4 list-disc">
                        {warningGuidance.map((msg) => (
                          <li key={msg}>{msg}</li>
                        ))}
                      </ul>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-fit rounded-lg gap-2"
                      onClick={handleOpenRuntimeSettings}
                    >
                      <Settings2 className="size-3.5" />
                      Configure Sandbox
                    </Button>
                  </div>
                </AlertDescription>
              </Alert>
            ) : null}

            <div
              data-slot="workspace-landing-composer"
              className="w-full rounded-3xl border border-border/60 bg-card/50 p-1.5 shadow-lg shadow-black/[0.03] backdrop-blur-sm transition-shadow hover:shadow-xl hover:shadow-black/[0.05]"
            >
              {composer}
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* Transcript area */}
          <div className="flex-1 min-h-0" data-slot="workspace-transcript">
            <WorkspaceMessageList
              messages={messages}
              isTyping={isTyping}
              isMobile={isMobile}
              showEmptyState={isMobile}
              onSuggestionClick={setInputValue}
              onResolveHitl={resolveHitl}
              onResolveClarification={resolveClarification}
            />
          </div>

          {/* Composer footer */}
          <div
            data-slot="workspace-composer"
            className={cn(
              "shrink-0 border-t border-border/30 bg-gradient-to-t from-background via-background/95 to-background/80 px-4 pb-6 pt-4 md:px-6",
              "backdrop-blur-sm",
            )}
          >
            <div className="mx-auto w-full max-w-200">
              <div className="flex flex-col gap-3">
                {showRuntimeWarning ? (
                  <Alert className="border-amber-500/25 bg-amber-500/5 text-foreground rounded-lg">
                    <TriangleAlert className="text-amber-500 size-4" />
                    <AlertTitle className="text-sm font-medium">{runtimeWarningTitle}</AlertTitle>
                    <AlertDescription>
                      <div className="mt-1 flex flex-col gap-3">
                        <div className="flex items-start justify-between gap-4">
                          <p className="text-xs text-muted-foreground leading-relaxed">
                            Connect to a Daytona sandbox to enable secure code execution. Your code
                            runs in an isolated environment with persistent storage.
                          </p>
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 shrink-0 gap-1.5 rounded-lg text-xs"
                            onClick={handleOpenRuntimeSettings}
                          >
                            <Settings2 className="size-3" />
                            Configure
                          </Button>
                        </div>
                        {warningGuidance.length > 0 ? (
                          <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground/80">
                            {warningGuidance.map((msg) => (
                              <li key={msg}>{msg}</li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    </AlertDescription>
                  </Alert>
                ) : null}
                <div className="mx-auto w-full max-w-175">{composer}</div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
