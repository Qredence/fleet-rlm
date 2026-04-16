import type {
  ChatMessage,
  ChatQueueItem,
  ChatRenderPart,
  ChatTraceStep,
} from "@/lib/workspace/workspace-types";
import type { WsServerEvent, WsServerMessage } from "@/lib/rlm-api";
import { createLocalId } from "@/lib/id";
import { QueryClient } from "@tanstack/react-query";
import {
  asOptionalNumber,
  asOptionalText,
  asRecord,
  parseRuntimeContext,
} from "@/lib/workspace/backend-chat-event-payload";
import { useWorkspaceUiStore } from "@/lib/workspace/workspace-ui-store";
import { attachFinalReferences } from "@/lib/workspace/backend-chat-event-references";
import {
  normalizeTrajectorySteps,
  normalizeTrajectoryStepsFromFinalPayload,
  trajectoryStepDetails,
  type NormalizedTrajectoryStep,
} from "@/lib/workspace/backend-chat-event-trajectory";
import {
  appendToolLikePart,
  inferStatusTone,
  sandboxProgressPartFromStatus,
} from "@/lib/workspace/backend-chat-event-tool-parts";

const DEFAULT_PHASE = 1 as const;
interface ApplyFrameResult {
  messages: ChatMessage[];
  terminal: boolean;
  errored: boolean;
}

function nextId(prefix: string): string {
  return createLocalId(prefix);
}

function appendSystem(messages: ChatMessage[], text: string): ChatMessage[] {
  if (!text.trim()) return messages;
  return [
    ...messages,
    {
      id: nextId("sys"),
      type: "system",
      content: text,
      phase: DEFAULT_PHASE,
    },
  ];
}

function latestStreamingAssistantIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg?.type === "assistant" && msg.streaming) return i;
  }
  return -1;
}

function latestTraceIndex(
  messages: ChatMessage[],
  predicate: (part: ChatRenderPart) => boolean,
): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (!msg || msg.type !== "trace" || !msg.renderParts) continue;
    if (msg.renderParts.some(predicate)) return i;
  }
  return -1;
}

function ensureStreamingAssistant(messages: ChatMessage[]): ChatMessage[] {
  if (latestStreamingAssistantIndex(messages) >= 0) return messages;
  return [
    ...messages,
    {
      id: nextId("assistant"),
      type: "assistant",
      content: "",
      streaming: true,
      phase: DEFAULT_PHASE,
    },
  ];
}

function appendAssistantToken(messages: ChatMessage[], token: string): ChatMessage[] {
  if (!token) return messages;
  const withAssistant = ensureStreamingAssistant(messages);
  const idx = latestStreamingAssistantIndex(withAssistant);
  if (idx < 0) return withAssistant;

  const copy = [...withAssistant];
  const target = copy[idx];
  if (!target) return withAssistant;
  copy[idx] = { ...target, content: `${target.content}${token}` };
  return copy;
}

function upsertReasoningRenderPart(
  msg: ChatMessage,
  parts: { type: "text"; text: string }[],
  isThinking: boolean,
  duration?: number,
): ChatMessage {
  const nextPart: ChatRenderPart = {
    kind: "reasoning",
    parts,
    isStreaming: isThinking,
    duration,
  };
  return {
    ...msg,
    renderParts: [nextPart],
  };
}

function finishReasoning(messages: ChatMessage[]): ChatMessage[] {
  let updated = false;
  const next = messages.map((msg) => {
    if (msg.type !== "reasoning" || !msg.reasoningData?.isThinking) return msg;
    updated = true;
    const nextMsg = {
      ...msg,
      reasoningData: {
        ...msg.reasoningData,
        isThinking: false,
      },
    };
    return upsertReasoningRenderPart(
      nextMsg,
      nextMsg.reasoningData.parts,
      false,
      nextMsg.reasoningData.duration,
    );
  });
  return updated ? next : messages;
}

function completeAssistant(messages: ChatMessage[], text: string): ChatMessage[] {
  const idx = latestStreamingAssistantIndex(messages);

  if (idx >= 0) {
    const copy = [...messages];
    const current = copy[idx];
    if (!current) return messages;
    copy[idx] = {
      ...current,
      content: text || current.content,
      streaming: false,
    };
    return copy;
  }

  if (!text.trim()) return messages;

  return [
    ...messages,
    {
      id: nextId("assistant"),
      type: "assistant",
      content: text,
      streaming: false,
      phase: DEFAULT_PHASE,
    },
  ];
}

function preferredFinalArtifactText(value: unknown): string | undefined {
  const direct = asOptionalText(value);
  if (direct) return direct;

  const record = asRecord(value);
  if (!record) return undefined;

  for (const key of ["final_markdown", "summary", "text", "content", "message"]) {
    const candidate = asOptionalText(record[key]);
    if (candidate) return candidate;
  }

  const nestedValue = record.value;
  if (nestedValue !== value) {
    return preferredFinalArtifactText(nestedValue);
  }

  return undefined;
}

function resolveFinalAssistantText(text: string, payload?: Record<string, unknown>): string {
  const runResult = asRecord(payload?.run_result ?? payload?.runResult);
  const preferred =
    preferredFinalArtifactText(payload?.final_artifact ?? payload?.finalArtifact) ??
    preferredFinalArtifactText(runResult?.final_artifact ?? runResult?.finalArtifact);

  return preferred ?? text;
}

function readGuardrailWarnings(payload: Record<string, unknown> | undefined): string[] {
  const raw = payload?.guardrail_warnings;
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => (typeof item === "string" ? item.trim() : "")).filter(Boolean);
}

function appendTracePart(
  messages: ChatMessage[],
  part: ChatRenderPart,
  content = "",
  traceSource: ChatMessage["traceSource"] = "live",
): ChatMessage[] {
  return [
    ...messages,
    {
      id: nextId("trace"),
      type: "trace",
      content,
      traceSource,
      phase: DEFAULT_PHASE,
      renderParts: [part],
    },
  ];
}

function currentTurnStartIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i]?.type === "user") return i;
  }
  return -1;
}

function currentTurnMessages(messages: ChatMessage[]): ChatMessage[] {
  const start = currentTurnStartIndex(messages);
  return start >= 0 ? messages.slice(start + 1) : messages;
}

function hasLiveTraceInCurrentTurn(messages: ChatMessage[]): boolean {
  return currentTurnMessages(messages).some((message) => {
    if (message.traceSource !== "live") return false;
    return message.type === "trace" || message.type === "reasoning";
  });
}

function appendReasoningEvent(
  messages: ChatMessage[],
  text: string,
  traceSource: ChatMessage["traceSource"],
  payload?: Record<string, unknown>,
  label = "reasoning",
): ChatMessage[] {
  if (text.length === 0) return messages;
  const runtimeContext = parseRuntimeContext(payload);
  const resolvedLabel = asOptionalText(
    payload?.reasoning_label ?? payload?.reasoningLabel ?? payload?.label,
  );
  return appendTracePart(
    messages,
    {
      kind: "reasoning",
      parts: [{ type: "text", text }],
      isStreaming: false,
      label: resolvedLabel ?? label,
      ...(runtimeContext ? { runtimeContext } : {}),
    },
    text,
    traceSource,
  );
}

function appendTaskTrace(
  messages: ChatMessage[],
  task: Extract<ChatRenderPart, { kind: "task" }>,
  content: string,
  traceSource: ChatMessage["traceSource"] = "live",
): ChatMessage[] {
  return appendTracePart(messages, task, content, traceSource);
}

function appendStatusTrace(
  messages: ChatMessage[],
  text: string,
  tone: Extract<ChatRenderPart, { kind: "status_note" }>["tone"] = "neutral",
  payload?: Record<string, unknown>,
  traceSource: ChatMessage["traceSource"] = "live",
): ChatMessage[] {
  const trimmed = text.trim();
  if (!trimmed) return messages;
  const toolName = asOptionalText(payload?.tool_name ?? payload?.toolName);
  const stepIndex = asOptionalNumber(payload?.step_index ?? payload?.stepIndex);
  const runtimeContext = parseRuntimeContext(payload);
  return appendTracePart(
    messages,
    {
      kind: "status_note",
      text: trimmed,
      tone,
      ...(toolName ? { toolName } : {}),
      ...(stepIndex != null ? { stepIndex } : {}),
      ...(runtimeContext ? { runtimeContext } : {}),
    },
    trimmed,
    traceSource,
  );
}

function upsertQueue(messages: ChatMessage[], text: string): ChatMessage[] {
  const label = text.trim() || "Plan update";
  const idx = latestTraceIndex(messages, (part) => part.kind === "queue");
  const queueItem: ChatQueueItem = {
    id: nextId("queue-item"),
    label,
    completed: false,
  };

  if (idx < 0) {
    return appendTracePart(
      messages,
      {
        kind: "queue",
        title: "Plan",
        items: [queueItem],
      },
      text,
      "summary",
    );
  }

  const copy = [...messages];
  const msg = copy[idx];
  if (!msg?.renderParts?.length) return messages;
  const nextParts = msg.renderParts.map((part) => {
    if (part.kind !== "queue") return part;
    return { ...part, items: [...part.items, queueItem] };
  });
  copy[idx] = {
    ...msg,
    content: label,
    traceSource: "summary",
    renderParts: nextParts,
  };
  return copy;
}

function isDaytonaPayload(payload?: Record<string, unknown>): boolean {
  const runtime = asRecord(payload?.runtime);
  const runtimeMode =
    asOptionalText(payload?.runtime_mode) ?? asOptionalText(runtime?.runtime_mode);
  return runtimeMode === "daytona_pilot";
}

function upsertChainOfThought(
  messages: ChatMessage[],
  step: NormalizedTrajectoryStep,
): ChatMessage[] {
  const traceStep: ChatTraceStep = {
    id: `trajectory-step-${step.index}`,
    index: step.index,
    label: step.label,
    body: step.thought,
    status: "active",
    details: trajectoryStepDetails(step),
  };

  const idx = latestTraceIndex(messages, (part) => part.kind === "chain_of_thought");
  if (idx < 0) {
    return appendTracePart(
      messages,
      {
        kind: "chain_of_thought",
        title: "Trajectory",
        steps: [traceStep],
      },
      step.label,
      "summary",
    );
  }

  const copy = [...messages];
  const msg = copy[idx];
  if (!msg?.renderParts) return messages;
  const nextParts = msg.renderParts.map((part) => {
    if (part.kind !== "chain_of_thought") return part;
    const withoutCurrent = part.steps.filter((s) => s.index !== step.index);
    const sortedSteps = [...withoutCurrent, traceStep].sort((left, right) => {
      const leftIndex = typeof left.index === "number" ? left.index : Number.POSITIVE_INFINITY;
      const rightIndex = typeof right.index === "number" ? right.index : Number.POSITIVE_INFINITY;
      if (leftIndex !== rightIndex) return leftIndex - rightIndex;
      return left.id.localeCompare(right.id);
    });
    const updatedSteps = sortedSteps.map((candidate) => ({
      ...candidate,
      status: candidate.index === step.index ? ("active" as const) : ("complete" as const),
    }));
    return { ...part, steps: updatedSteps };
  });
  copy[idx] = {
    ...msg,
    content: step.label,
    traceSource: "summary",
    renderParts: nextParts,
  };
  return copy;
}

function applyTrajectoryStep(
  messages: ChatMessage[],
  step: NormalizedTrajectoryStep,
  includePrimaryFallback = false,
): ChatMessage[] {
  let next = messages;
  if (includePrimaryFallback && step.thought) {
    next = appendReasoningEvent(
      next,
      step.thought,
      "trajectory",
      undefined,
      `thought_${step.index}`,
    );
  }
  next = upsertChainOfThought(next, step);
  return next;
}

function applyTrajectoryEvent(
  messages: ChatMessage[],
  text: string,
  payload?: Record<string, unknown>,
): ChatMessage[] {
  const steps = normalizeTrajectorySteps(text, payload);
  if (steps.length === 0) return messages;

  const includePrimaryFallback = !hasLiveTraceInCurrentTurn(messages) || isDaytonaPayload(payload);
  return steps.reduce<ChatMessage[]>(
    (acc, step) => applyTrajectoryStep(acc, step, includePrimaryFallback),
    messages,
  );
}

function appendFinalTrajectoryThoughts(
  messages: ChatMessage[],
  payload?: Record<string, unknown>,
): ChatMessage[] {
  const steps = normalizeTrajectoryStepsFromFinalPayload(payload);
  if (steps.length === 0) return messages;

  return steps.reduce<ChatMessage[]>((acc, step) => {
    if (!step.thought) return acc;
    return appendReasoningEvent(acc, step.thought, "summary", payload, `thought_${step.index}`);
  }, messages);
}

function finalizeTraceParts(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((msg) => {
    if (msg.type !== "trace" || !msg.renderParts) return msg;
    const renderParts = msg.renderParts.map((part) => {
      switch (part.kind) {
        case "chain_of_thought":
          return {
            ...part,
            steps: part.steps.map((s) =>
              s.status === "active" ? { ...s, status: "complete" as const } : s,
            ),
          };
        case "queue":
          return {
            ...part,
            items: part.items.map((it) => ({ ...it, completed: true })),
          };
        case "task":
          return part.status === "in_progress" ? { ...part, status: "completed" as const } : part;
        case "tool":
        case "sandbox":
          return part.state === "running" || part.state === "input-streaming"
            ? { ...part, state: "output-available" as const }
            : part;
        default:
          return part;
      }
    });
    return { ...msg, renderParts };
  });
}

function resolveHitlByMessageId(
  messages: ChatMessage[],
  messageId: string,
  resolution: string,
): ChatMessage[] {
  let changed = false;
  const next = messages.map((msg) => {
    if (changed || msg.id !== messageId || msg.type !== "hitl" || !msg.hitlData) {
      return msg;
    }
    changed = true;
    return {
      ...msg,
      hitlData: {
        ...msg.hitlData,
        resolved: true,
        resolvedLabel: resolution,
      },
    };
  });
  return changed ? next : messages;
}

function rollbackHitlByMessageId(messages: ChatMessage[], messageId: string): ChatMessage[] {
  let changed = false;
  const next = messages.map((msg) => {
    if (changed || msg.id !== messageId || msg.type !== "hitl" || !msg.hitlData) {
      return msg;
    }
    changed = true;
    return {
      ...msg,
      hitlData: {
        ...msg.hitlData,
        resolved: false,
        resolvedLabel: undefined,
      },
    };
  });
  return changed ? next : messages;
}

function applyEvent(
  messages: ChatMessage[],
  frame: WsServerEvent,
  queryClient?: QueryClient,
): ApplyFrameResult {
  const { kind, text, payload } = frame.data;

  switch (kind) {
    case "assistant_token": {
      return {
        messages: appendAssistantToken(messages, text),
        terminal: false,
        errored: false,
      };
    }
    case "reasoning_step": {
      return {
        messages: appendReasoningEvent(messages, text, "live", payload),
        terminal: false,
        errored: false,
      };
    }
    case "trajectory_step": {
      return {
        messages: applyTrajectoryEvent(messages, text, payload),
        terminal: false,
        errored: false,
      };
    }
    case "status": {
      const sandboxPart = sandboxProgressPartFromStatus(payload);
      if (sandboxPart) {
        return {
          messages: appendTracePart(messages, sandboxPart, text),
          terminal: false,
          errored: false,
        };
      }
      return {
        messages: appendStatusTrace(messages, text, inferStatusTone(text, payload), payload),
        terminal: false,
        errored: false,
      };
    }
    case "warning": {
      return {
        messages: appendStatusTrace(messages, text, "warning", payload),
        terminal: false,
        errored: false,
      };
    }
    case "tool_call": {
      return {
        messages: appendToolLikePart(messages, "tool_call", text, payload, appendTracePart),
        terminal: false,
        errored: false,
      };
    }
    case "tool_result": {
      return {
        messages: appendToolLikePart(messages, "tool_result", text, payload, appendTracePart),
        terminal: false,
        errored: false,
      };
    }
    case "plan_update": {
      const label = text.trim() || "Running plan...";
      let next = appendTaskTrace(
        messages,
        {
          kind: "task",
          title: "Plan update",
          status: "in_progress",
          items: [{ id: nextId("task-item"), text: label }],
        },
        label,
      );
      next = upsertQueue(next, label);
      return { messages: next, terminal: false, errored: false };
    }
    case "rlm_executing": {
      let next = messages;
      const toolName =
        typeof payload?.tool_name === "string" && payload.tool_name
          ? payload.tool_name
          : "Sub-agent iteration";
      next = appendTaskTrace(
        next,
        {
          kind: "task",
          title: `Executing ${toolName}`,
          status: "in_progress",
          items: text ? [{ id: nextId("task-item"), text }] : undefined,
        },
        `Executing ${toolName}...`,
      );
      return { messages: next, terminal: false, errored: false };
    }
    case "memory_update": {
      const next = appendTaskTrace(
        messages,
        {
          kind: "task",
          title: text || "Updating memory...",
          status: "completed",
          items: text ? [{ id: nextId("task-item"), text }] : undefined,
        },
        text || "Updating memory...",
      );

      if (text.trim()) {
        useWorkspaceUiStore.getState().addMemoryEntry({
          content: text.trim(),
          timestamp:
            typeof frame.data.timestamp === "string"
              ? frame.data.timestamp
              : new Date().toISOString(),
        });
      }

      if (queryClient) {
        queryClient.invalidateQueries({ queryKey: ["memory"] });
      }
      return { messages: next, terminal: false, errored: false };
    }
    case "hitl_request": {
      const hitlPayload = asRecord(payload?.hitl ?? payload);
      const question = asOptionalText(hitlPayload?.question) || text.trim() || "Approval needed";
      const messageId =
        asOptionalText(hitlPayload?.message_id ?? hitlPayload?.messageId) ?? nextId("hitl");
      const rawActions = hitlPayload?.actions;
      const actions = Array.isArray(rawActions)
        ? rawActions
            .map((item) => {
              const rec = asRecord(item);
              if (!rec) return null;
              const label = asOptionalText(rec.label);
              if (!label) return null;
              const variant = asOptionalText(rec.variant);
              return {
                label,
                variant: variant === "primary" || variant === "secondary" ? variant : "secondary",
              } as const;
            })
            .filter(
              (value): value is { label: string; variant: "primary" | "secondary" } =>
                value != null,
            )
        : [];

      useWorkspaceUiStore.getState().setPendingHitlMessageId(messageId);
      return {
        messages: [
          ...messages,
          {
            id: messageId,
            type: "hitl",
            content: question,
            phase: DEFAULT_PHASE,
            hitlData: {
              question,
              actions:
                actions.length > 0
                  ? actions
                  : [
                      { label: "Approve", variant: "primary" },
                      { label: "Reject", variant: "secondary" },
                    ],
            },
          },
        ],
        terminal: false,
        errored: false,
      };
    }
    case "hitl_resolved": {
      const messageId = asOptionalText(payload?.message_id ?? payload?.messageId);
      const resolution =
        asOptionalText(payload?.resolution) ?? asOptionalText(payload?.label) ?? text.trim();
      if (!resolution) return { messages, terminal: false, errored: false };

      useWorkspaceUiStore.getState().setPendingHitlMessageId(null);

      if (messageId) {
        return {
          messages: resolveHitlByMessageId(messages, messageId, resolution),
          terminal: false,
          errored: false,
        };
      }

      let updated = false;
      const next = messages.map((msg) => {
        if (updated || msg.type !== "hitl" || !msg.hitlData || msg.hitlData.resolved) {
          return msg;
        }
        updated = true;
        return {
          ...msg,
          hitlData: {
            ...msg.hitlData,
            resolved: true,
            resolvedLabel: resolution,
          },
        };
      });
      return { messages: next, terminal: false, errored: false };
    }
    case "command_ack": {
      const command = asOptionalText(payload?.command);
      const result = asRecord(payload?.result);
      const messageId = asOptionalText(result?.message_id ?? result?.messageId);
      const resolution = asOptionalText(result?.resolution) ?? asOptionalText(result?.action_label);
      let next = messages;
      if (command === "resolve_hitl" && messageId && resolution) {
        next = resolveHitlByMessageId(next, messageId, resolution);
        useWorkspaceUiStore.getState().setPendingHitlMessageId(null);
      }
      return {
        messages: appendTracePart(
          next,
          {
            kind: "status_note",
            tone: "success",
            text: text || "Action acknowledged",
          },
          text || "Action acknowledged",
        ),
        terminal: false,
        errored: false,
      };
    }
    case "command_reject": {
      const command = asOptionalText(payload?.command);
      const result = asRecord(payload?.result);
      const messageId = asOptionalText(result?.message_id ?? result?.messageId);
      let next = messages;
      if (command === "resolve_hitl" && messageId) {
        next = rollbackHitlByMessageId(next, messageId);
      }
      return {
        messages: appendTracePart(
          next,
          {
            kind: "status_note",
            tone: "error",
            text: text || "Action rejected",
          },
          text || "Action rejected",
        ),
        terminal: false,
        errored: false,
      };
    }
    case "final": {
      let next = completeAssistant(messages, resolveFinalAssistantText(text, payload));
      next = finishReasoning(next);
      next = finalizeTraceParts(next);
      next = appendFinalTrajectoryThoughts(next, payload);

      const finalReasoning =
        typeof payload?.final_reasoning === "string" ? payload.final_reasoning.trim() : "";
      if (finalReasoning) {
        next = appendReasoningEvent(next, finalReasoning, "summary", payload, "final_reasoning");
      }

      next = attachFinalReferences(next, payload);

      const warnings = readGuardrailWarnings(payload);
      if (warnings.length > 0) {
        next = appendSystem(next, `Guardrail warnings:\n- ${warnings.join("\n- ")}`);
      }

      return { messages: next, terminal: true, errored: false };
    }
    case "cancelled": {
      let next = finishReasoning(messages);
      next = finalizeTraceParts(next);
      next = appendSystem(next, text || "Request cancelled.");
      return { messages: next, terminal: true, errored: false };
    }
    case "error": {
      let next = finishReasoning(messages);
      next = finalizeTraceParts(next);
      next = appendSystem(next, `Backend error: ${text || "Unknown server error."}`);
      return { messages: next, terminal: true, errored: true };
    }
    default: {
      return { messages, terminal: false, errored: false };
    }
  }
}

export function applyWsFrameToMessages(
  messages: ChatMessage[],
  frame: WsServerMessage,
  queryClient?: QueryClient,
): ApplyFrameResult {
  if (frame.type === "error") {
    const next = finalizeTraceParts(appendSystem(messages, `Backend error: ${frame.message}`));
    return { messages: finishReasoning(next), terminal: true, errored: true };
  }

  return applyEvent(messages, frame, queryClient);
}
