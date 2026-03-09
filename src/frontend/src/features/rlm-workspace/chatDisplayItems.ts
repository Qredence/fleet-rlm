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
type AttachableTracePart = Exclude<
  ChatRenderPart,
  {
    kind: "reasoning" | "chain_of_thought" | "confirmation";
  }
>;

type ReasoningTracePart = Extract<ChatRenderPart, { kind: "reasoning" }>;
type TrajectoryTracePart = Extract<ChatRenderPart, { kind: "chain_of_thought" }>;

export interface AssistantTurnReasoningItem {
  key: string;
  message: ChatMessage;
  part: ReasoningTracePart;
}

export interface AssistantTurnTrajectoryItem {
  key: string;
  message: ChatMessage;
  part: TrajectoryTracePart;
}

export interface AssistantTurnTracePartItem {
  key: string;
  message: ChatMessage;
  part: AttachableTracePart;
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
  turnId: string;
  message?: ChatMessage;
  isPendingShell: boolean;
  reasoningItems: AssistantTurnReasoningItem[];
  trajectoryItems: AssistantTurnTrajectoryItem[];
  attachedToolSessions: Array<Extract<TraceDisplayItem, { kind: "tool_session" }>>;
  attachedTraceParts: AssistantTurnTracePartItem[];
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

function isAttachableTracePart(
  part: ChatRenderPart,
): part is AttachableTracePart {
  return (
    part.kind !== "reasoning" &&
    part.kind !== "chain_of_thought" &&
    part.kind !== "confirmation"
  );
}

function createAssistantTurn(
  key: string,
  turnId: string,
  options?: {
    message?: ChatMessage;
    isPendingShell?: boolean;
  },
): AssistantTurnDisplayItem {
  return {
    kind: "assistant_turn",
    key,
    turnId,
    message: options?.message,
    isPendingShell: options?.isPendingShell ?? false,
    reasoningItems: [],
    trajectoryItems: [],
    attachedToolSessions: [],
    attachedTraceParts: [],
  };
}

export function buildPendingAssistantTurnId(userMessageId: string) {
  return `pending:${userMessageId}`;
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
          activeReasoningTurn = createAssistantTurn(key, key);
        }
        activeReasoningTurn.reasoningItems.push({ key, message, part });
        continue;
      }

      if (part.kind === "chain_of_thought") {
        flushActiveSession();
        if (!activeReasoningTurn) {
          activeReasoningTurn = createAssistantTurn(key, key);
        }
        activeReasoningTurn.trajectoryItems.push({ key, message, part });
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
    item.attachedToolSessions.length === 0 &&
    item.attachedTraceParts.length === 0 &&
    (item.reasoningItems.length > 0 || item.trajectoryItems.length > 0) &&
    item.reasoningItems.every(
      (reasoningItem) => reasoningItem.message.traceSource === "summary",
    ) &&
    item.trajectoryItems.every(
      (trajectoryItem) => trajectoryItem.message.traceSource === "summary",
    )
  );
}

function isReasoningOnlyAssistantTurn(
  item: TraceDisplayItem | AssistantTurnDisplayItem | undefined,
): item is AssistantTurnDisplayItem {
  return (
    item?.kind === "assistant_turn" &&
    item.message == null &&
    item.attachedToolSessions.length === 0 &&
    item.attachedTraceParts.length === 0 &&
    (item.reasoningItems.length > 0 || item.trajectoryItems.length > 0)
  );
}

function attachPendingTraceItems(
  pending: Array<TraceDisplayItem | AssistantTurnDisplayItem>,
  targetTurn: AssistantTurnDisplayItem,
) {
  const remainingPending: TraceDisplayItem[] = [];

  for (const pendingItem of pending) {
    if (pendingItem.kind === "assistant_turn" && isReasoningOnlyAssistantTurn(pendingItem)) {
      targetTurn.reasoningItems.push(...pendingItem.reasoningItems);
      targetTurn.trajectoryItems.push(...pendingItem.trajectoryItems);
      continue;
    }

    if (pendingItem.kind === "tool_session") {
      targetTurn.attachedToolSessions.push(pendingItem);
      continue;
    }

    const attachableParts = pendingItem.renderParts.flatMap((part) =>
      isAttachableTracePart(part)
        ? [{ key: pendingItem.key, message: pendingItem.message, part }]
        : [],
    );
    if (attachableParts.length > 0) {
      targetTurn.attachedTraceParts.push(...attachableParts);
      const remainingParts = pendingItem.renderParts.filter(
        (part) => !isAttachableTracePart(part),
      );
      if (remainingParts.length > 0) {
        remainingPending.push({
          ...pendingItem,
          renderParts: remainingParts,
        });
      }
      continue;
    }

    remainingPending.push(pendingItem);
  }

  return remainingPending;
}

interface BuildChatDisplayItemsOptions {
  showPendingAssistantShell?: boolean;
}

export function buildChatDisplayItems(
  messages: ChatMessage[],
  options?: BuildChatDisplayItemsOptions,
): ChatDisplayItem[] {
  const items: ChatDisplayItem[] = [];
  let pendingTraceMessages: ChatMessage[] = [];
  let currentTurnUserMessageId: string | null = null;
  let activeAssistantTurn: AssistantTurnDisplayItem | null = null;
  const showPendingAssistantShell = options?.showPendingAssistantShell ?? false;

  const flushPendingTraceMessages = (targetTurn?: AssistantTurnDisplayItem) => {
    if (pendingTraceMessages.length === 0) return false;
    const pending = buildTraceDisplayItems(pendingTraceMessages).items;
    const remainingPending = targetTurn
      ? attachPendingTraceItems(pending, targetTurn)
      : pending;

    if (!targetTurn) {
      const lastItem = items[items.length - 1];
      if (lastItem?.kind === "assistant_turn" && lastItem.message) {
        const trailingSummaryReasoning = remainingPending.filter(
          (pendingItem) => isSummaryReasoningOnlyAssistantTurn(pendingItem),
        );
        if (trailingSummaryReasoning.length > 0) {
          lastItem.reasoningItems.push(
            ...trailingSummaryReasoning.flatMap((pendingItem) => pendingItem.reasoningItems),
          );
          lastItem.trajectoryItems.push(
            ...trailingSummaryReasoning.flatMap((pendingItem) => pendingItem.trajectoryItems),
          );
          const nonSummaryPending = remainingPending.filter(
            (pendingItem) => !isSummaryReasoningOnlyAssistantTurn(pendingItem),
          );
          items.push(...nonSummaryPending);
          pendingTraceMessages = [];
          return true;
        }
      }
    }

    items.push(...remainingPending);
    pendingTraceMessages = [];
    return true;
  };

  const finalizeCurrentTurn = () => {
    if (pendingTraceMessages.length === 0) {
      if (
        showPendingAssistantShell &&
        currentTurnUserMessageId &&
        !activeAssistantTurn
      ) {
        const pendingTurn = createAssistantTurn(
          buildPendingAssistantTurnId(currentTurnUserMessageId),
          buildPendingAssistantTurnId(currentTurnUserMessageId),
          { isPendingShell: true },
        );
        items.push(pendingTurn);
        activeAssistantTurn = pendingTurn;
      }
      return;
    }

    if (activeAssistantTurn) {
      flushPendingTraceMessages(activeAssistantTurn);
      return;
    }

    if (showPendingAssistantShell && currentTurnUserMessageId) {
      const pendingTurn = createAssistantTurn(
        buildPendingAssistantTurnId(currentTurnUserMessageId),
        buildPendingAssistantTurnId(currentTurnUserMessageId),
        { isPendingShell: true },
      );
      flushPendingTraceMessages(pendingTurn);
      items.push(pendingTurn);
      activeAssistantTurn = pendingTurn;
      return;
    }

    flushPendingTraceMessages();
  };

  for (const message of messages) {
    if (message.type === "trace" && message.renderParts?.length) {
      pendingTraceMessages.push(message);
      continue;
    }

    if (message.type === "user") {
      finalizeCurrentTurn();
      activeAssistantTurn = null;
      currentTurnUserMessageId = message.id;
      items.push({
        kind: "message",
        key: message.id,
        message,
      });
      continue;
    }

    if (message.type === "assistant") {
      const assistantTurn: AssistantTurnDisplayItem =
        activeAssistantTurn?.isPendingShell
          ? activeAssistantTurn
          : createAssistantTurn(message.id, message.id, {
              message,
            });
      flushPendingTraceMessages(assistantTurn);
      assistantTurn.key = message.id;
      assistantTurn.turnId = message.id;
      assistantTurn.message = message;
      assistantTurn.isPendingShell = false;
      if (assistantTurn !== activeAssistantTurn) {
        items.push(assistantTurn);
      }
      activeAssistantTurn = assistantTurn;
      continue;
    }

    if (message.type !== "trace") {
      finalizeCurrentTurn();
    }
    items.push({
      kind: "message",
      key: message.id,
      message,
    });
  }

  finalizeCurrentTurn();
  return items;
}
