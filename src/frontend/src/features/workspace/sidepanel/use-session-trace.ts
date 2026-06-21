import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { QueryFunctionContext } from "@tanstack/react-query";

import { buildChatDisplayItems } from "@/lib/workspace/chat-display-items";
import { type AssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import { useChatStore, useWorkspaceUiStore } from "@/features/workspace/use-workspace";
import {
  resolveSessionTraceTarget,
  resolveWorkspaceTraceSessionScope,
  getTraceSpanStatus,
} from "@/features/workspace/screen/workspace-session-trace-model";
import { sessionsEndpoints } from "@/lib/rlm-api/sessions";
import { executionSectionState, statusTone } from "@/features/workspace/inspection/inspector-ui";

export const sessionTraceQueryKeys = {
  all: ["workspace", "sidepanel"] as const,
  traces: (sessionId: string | null) =>
    [...sessionTraceQueryKeys.all, "session-traces", sessionId ?? "none"] as const,
  traceDebug: (sessionId: string | null, traceId: string | null, clientRequestId: string | null) =>
    [
      ...sessionTraceQueryKeys.all,
      "trace-debug",
      sessionId ?? "none",
      traceId,
      clientRequestId,
    ] as const,
};

export const sessionTraceQueryOptions = {
  traces: (sessionId: string | null) => ({
    queryKey: sessionTraceQueryKeys.traces(sessionId),
    queryFn: ({ signal }: QueryFunctionContext) => {
      if (!sessionId)
        return { items: [] } as unknown as Awaited<ReturnType<typeof sessionsEndpoints.traces>>;
      return sessionsEndpoints.traces(sessionId, { limit: 12, offset: 0 }, signal);
    },
    retry: false,
  }),
  traceDebug: (
    sessionId: string | null,
    traceId: string | null,
    clientRequestId: string | null,
  ) => ({
    queryKey: sessionTraceQueryKeys.traceDebug(sessionId, traceId, clientRequestId),
    queryFn: ({ signal }: QueryFunctionContext) => {
      if (!sessionId)
        return null as unknown as Awaited<ReturnType<typeof sessionsEndpoints.traceDebug>>;
      return sessionsEndpoints.traceDebug(
        sessionId,
        { traceId: traceId ?? undefined, clientRequestId: clientRequestId ?? undefined },
        signal,
      );
    },
    retry: false,
  }),
};

type AssistantTurn = Extract<
  ReturnType<typeof buildChatDisplayItems>[number],
  { kind: "assistant_turn" }
>;

export function selectedTurnStatus(
  model: AssistantContentModel,
): "pending" | "running" | "completed" | "failed" {
  if (model.execution.sections.some((section) => executionSectionState(section) === "failed")) {
    return "failed";
  }
  if (model.trajectory.items.some((item) => item.status === "failed")) return "failed";
  if (
    model.answer.showStreamingShell ||
    model.execution.sections.some((section) => {
      const state = executionSectionState(section);
      return state === "pending" || state === "running";
    }) ||
    model.trajectory.items.some((item) => item.status === "pending" || item.status === "running") ||
    model.trajectory.overview?.isStreaming
  ) {
    return "running";
  }
  return "completed";
}

export function useSelectedWorkspaceTurn(): AssistantTurn | null {
  const messages = useChatStore((state) => state.messages);
  const isStreaming = useChatStore((state) => state.isStreaming);
  const selectedAssistantTurnId = useWorkspaceUiStore((state) => state.selectedAssistantTurnId);

  return useMemo(() => {
    const assistantTurns = buildChatDisplayItems(messages, {
      showPendingAssistantShell: isStreaming,
    }).filter((item) => item.kind === "assistant_turn") as AssistantTurn[];
    if (!selectedAssistantTurnId) return assistantTurns.at(-1) ?? null;
    return (assistantTurns.find(
      (item) => item.kind === "assistant_turn" && item.turnId === selectedAssistantTurnId,
    ) ?? null) as AssistantTurn | null;
  }, [isStreaming, messages, selectedAssistantTurnId]);
}

export function useSessionTraceState() {
  const sessionId = useChatStore((state) => state.sessionId);
  const runtimeSessionId = useChatStore((state) => state.runtimeSessionId);
  const durableSessionId = useChatStore((state) => state.durableSessionId);
  const messages = useChatStore((state) => state.messages);
  const traceScope = useMemo(
    () =>
      resolveWorkspaceTraceSessionScope({
        durableSessionId,
        runtimeSessionId,
        legacySessionId: sessionId,
      }),
    [durableSessionId, runtimeSessionId, sessionId],
  );
  const traceSessionId = traceScope.sessionId;
  const hasSessionContent = messages.length > 0;

  const tracesQuery = useQuery({
    ...sessionTraceQueryOptions.traces(traceSessionId),
    enabled: Boolean(traceSessionId && hasSessionContent),
  });

  const target = useMemo(
    () => resolveSessionTraceTarget(messages, tracesQuery.data?.items ?? []),
    [messages, tracesQuery.data?.items],
  );

  const traceDebugQuery = useQuery({
    ...sessionTraceQueryOptions.traceDebug(traceSessionId, target.traceId, target.clientRequestId),
    enabled: Boolean(
      traceSessionId && hasSessionContent && (target.traceId || target.clientRequestId),
    ),
  });

  return {
    sessionId,
    runtimeSessionId,
    durableSessionId,
    traceSessionId,
    hasSessionContent,
    traceScope,
    messages,
    tracesQuery,
    target,
    traceDebugQuery,
  };
}

export type SessionTraceState = ReturnType<typeof useSessionTraceState>;

export function traceStatusTone(status: ReturnType<typeof getTraceSpanStatus>) {
  if (status === "failed") return statusTone("failed");
  if (status === "running") return statusTone("running");
  if (status === "completed") return statusTone("completed");
  return statusTone("pending");
}
