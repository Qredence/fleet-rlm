import type {
  ChatMessage,
  ChatRenderPart,
  RuntimeContext,
} from "@/lib/data/types";

type ToolSessionEventKind = "tool_call" | "tool_result" | "status";

type GroupableTracePart = Extract<
  ChatRenderPart,
  {
    kind: "tool" | "sandbox" | "environment_variables" | "status_note";
  }
>;

type ReasoningTracePart = Extract<ChatRenderPart, { kind: "reasoning" }>;

export interface AssistantTurnReasoningItem {
  key: string;
  message: ChatMessage;
  part: ReasoningTracePart;
}

export interface ToolSessionItem {
  key: string;
  traceSource: ChatMessage["traceSource"];
  eventKind: ToolSessionEventKind;
  part: GroupableTracePart;
  toolName?: string;
  stepIndex?: number;
  runtimeContext?: RuntimeContext;
}

export type TraceDisplayItem =
  | {
      kind: "trace_message";
      key: string;
      message: ChatMessage;
      renderParts: ChatRenderPart[];
    }
  | {
      kind: "tool_session";
      key: string;
      items: ToolSessionItem[];
    };

export interface AssistantTurnDisplayItem {
  kind: "assistant_turn";
  key: string;
  message?: ChatMessage;
  reasoningItems: AssistantTurnReasoningItem[];
}

export type ChatDisplayItem =
  | {
      kind: "message";
      key: string;
      message: ChatMessage;
    }
  | AssistantTurnDisplayItem
  | TraceDisplayItem;

function isToolSessionTracePart(
  part: ChatRenderPart,
): part is GroupableTracePart {
  return (
    part.kind === "tool" ||
    part.kind === "sandbox" ||
    part.kind === "environment_variables" ||
    part.kind === "status_note"
  );
}

function canStartToolSession(part: GroupableTracePart) {
  return part.kind !== "status_note";
}

function toolSessionEventKindForPart(
  part: GroupableTracePart,
): ToolSessionEventKind {
  if (part.kind === "status_note") return "status";
  if (part.kind === "environment_variables") return "tool_result";
  return part.state === "running" || part.state === "input-streaming"
    ? "tool_call"
    : "tool_result";
}

function toolSessionToolName(part: GroupableTracePart): string | undefined {
  if (part.kind === "tool") return part.toolType || part.title || undefined;
  if (part.kind === "sandbox") return part.title || undefined;
  if (part.kind === "environment_variables") return part.title || undefined;
  return part.toolName;
}

function toolSessionStepIndex(part: GroupableTracePart): number | undefined {
  if (
    part.kind === "tool" ||
    part.kind === "sandbox" ||
    part.kind === "status_note"
  ) {
    return part.stepIndex;
  }
  return undefined;
}

function toolSessionRuntimeContext(
  part: GroupableTracePart,
): RuntimeContext | undefined {
  if (
    part.kind === "tool" ||
    part.kind === "sandbox" ||
    part.kind === "status_note"
  ) {
    return part.runtimeContext;
  }
  return undefined;
}

function normalizeToolSessionName(name?: string) {
  return name?.trim().toLowerCase();
}

function toolSessionItemsCompatible(
  active: ToolSessionItem,
  candidate: ToolSessionItem,
) {
  if (active.traceSource !== candidate.traceSource) return false;
  if (active.stepIndex != null && candidate.stepIndex != null) {
    return active.stepIndex === candidate.stepIndex;
  }
  const activeName = normalizeToolSessionName(active.toolName);
  const candidateName = normalizeToolSessionName(candidate.toolName);
  if (activeName && candidateName) return activeName === candidateName;
  return false;
}

function shouldAppendToActiveSession(
  activeSession: ToolSessionItem[],
  candidate: ToolSessionItem,
) {
  const first = activeSession[0];
  if (!first || !toolSessionItemsCompatible(first, candidate)) return false;
  if (candidate.eventKind === "tool_call") return false;
  return true;
}

function buildToolSessionItem(
  part: GroupableTracePart,
  key: string,
  traceSource: ChatMessage["traceSource"],
): ToolSessionItem {
  return {
    key,
    traceSource,
    eventKind: toolSessionEventKindForPart(part),
    part,
    toolName: toolSessionToolName(part),
    stepIndex: toolSessionStepIndex(part),
    runtimeContext: toolSessionRuntimeContext(part),
  };
}

function isReasoningTracePart(
  part: ChatRenderPart,
): part is ReasoningTracePart {
  return part.kind === "reasoning";
}

type PendingTraceDisplay = {
  items: Array<TraceDisplayItem | AssistantTurnDisplayItem>;
};

function buildTraceDisplayItems(messages: ChatMessage[]): PendingTraceDisplay {
  const items: Array<TraceDisplayItem | AssistantTurnDisplayItem> = [];
  let activeSession: ToolSessionItem[] | null = null;
  let activeReasoningTurn: AssistantTurnDisplayItem | null = null;

  const flushActiveSession = () => {
    if (!activeSession?.length) return;
    items.push({
      kind: "tool_session",
      key: activeSession[0]?.key ?? `tool-session-${items.length}`,
      items: activeSession,
    });
    activeSession = null;
  };

  const flushActiveReasoningTurn = () => {
    if (!activeReasoningTurn) return;
    items.push(activeReasoningTurn);
    activeReasoningTurn = null;
  };

  for (const message of messages) {
    if (message.type !== "trace" || !message.renderParts?.length) {
      flushActiveSession();
      flushActiveReasoningTurn();
      continue;
    }

    for (let idx = 0; idx < message.renderParts.length; idx += 1) {
      const part = message.renderParts[idx];
      if (!part) continue;
      const key = `${message.id}-${part.kind}-${idx}`;

      if (isReasoningTracePart(part)) {
        flushActiveSession();
        if (!activeReasoningTurn) {
          activeReasoningTurn = {
            kind: "assistant_turn",
            key,
            reasoningItems: [],
          };
        }
        activeReasoningTurn.reasoningItems.push({ key, message, part });
        continue;
      }

      flushActiveReasoningTurn();

      if (!isToolSessionTracePart(part)) {
        flushActiveSession();
        items.push({
          kind: "trace_message",
          key,
          message,
          renderParts: [part],
        });
        continue;
      }

      const candidate = buildToolSessionItem(part, key, message.traceSource);
      if (activeSession?.length) {
        if (shouldAppendToActiveSession(activeSession, candidate)) {
          activeSession.push(candidate);
          continue;
        }
        flushActiveSession();
      }

      if (canStartToolSession(part)) {
        activeSession = [candidate];
        continue;
      }

      items.push({
        kind: "trace_message",
        key,
        message,
        renderParts: [part],
      });
    }
  }

  flushActiveSession();
  flushActiveReasoningTurn();
  return { items };
}

function isSummaryReasoningOnlyAssistantTurn(
  item: ChatDisplayItem | undefined,
): item is AssistantTurnDisplayItem {
  return (
    item?.kind === "assistant_turn" &&
    item.message == null &&
    item.reasoningItems.length > 0 &&
    item.reasoningItems.every(
      (reasoningItem) => reasoningItem.message.traceSource === "summary",
    )
  );
}

export function buildChatDisplayItems(
  messages: ChatMessage[],
): ChatDisplayItem[] {
  const items: ChatDisplayItem[] = [];
  let pendingTraceMessages: ChatMessage[] = [];

  const flushPendingTraceMessages = (assistantMessage?: ChatMessage) => {
    if (pendingTraceMessages.length === 0) return false;
    const pending = buildTraceDisplayItems(pendingTraceMessages).items;

    if (assistantMessage) {
      const lastPending = pending[pending.length - 1];
      if (lastPending?.kind === "assistant_turn" && !lastPending.message) {
        lastPending.message = assistantMessage;
      } else {
        pending.push({
          kind: "assistant_turn",
          key: assistantMessage.id,
          message: assistantMessage,
          reasoningItems: [],
        });
      }
    }

    if (!assistantMessage) {
      const lastItem = items[items.length - 1];
      if (lastItem?.kind === "assistant_turn" && lastItem.message) {
        const remainingPending: Array<
          TraceDisplayItem | AssistantTurnDisplayItem
        > = [];
        let mergedSummaryReasoning = false;

        for (const pendingItem of pending) {
          if (!isSummaryReasoningOnlyAssistantTurn(pendingItem)) {
            remainingPending.push(pendingItem);
            continue;
          }
          lastItem.reasoningItems.push(...pendingItem.reasoningItems);
          mergedSummaryReasoning = true;
        }

        if (mergedSummaryReasoning) {
          pending.length = 0;
          pending.push(...remainingPending);
        }
      }
    }

    items.push(...pending);
    pendingTraceMessages = [];
    return true;
  };

  for (const message of messages) {
    if (message.type === "trace" && message.renderParts?.length) {
      pendingTraceMessages.push(message);
      continue;
    }

    if (message.type === "assistant") {
      const flushedPendingTrace = flushPendingTraceMessages(message);
      if (!flushedPendingTrace) {
        const lastItem = items[items.length - 1];
        if (lastItem?.kind === "assistant_turn" && !lastItem.message) {
          lastItem.message = message;
        } else {
          items.push({
            kind: "assistant_turn",
            key: message.id,
            message,
            reasoningItems: [],
          });
        }
      }
      continue;
    }

    flushPendingTraceMessages();
    items.push({
      kind: "message",
      key: message.id,
      message,
    });
  }

  flushPendingTraceMessages();
  return items;
}
