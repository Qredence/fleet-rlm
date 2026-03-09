import type {
  ChatMessage,
  ChatQueueItem,
  ChatRenderPart,
  ChatTraceStep,
  RuntimeContext,
} from "@/lib/data/types";
import type { WsServerEvent, WsServerMessage } from "@/lib/rlm-api";
import { createLocalId } from "@/lib/id";
import { QueryClient } from "@tanstack/react-query";
import { attachFinalReferences } from "@/features/rlm-workspace/backendChatEventReferences";
import {
  appendToolLikePart,
  inferStatusTone,
} from "@/features/rlm-workspace/backendChatEventToolParts";

const DEFAULT_PHASE = 1 as const;
interface ApplyFrameResult {
  messages: ChatMessage[];
  terminal: boolean;
  errored: boolean;
}

interface NormalizedTrajectoryStep {
  index: number;
  thought?: string;
  action?: string;
  toolName?: string;
  toolInput?: unknown;
  toolOutput?: unknown;
  label: string;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return undefined;
  return value as Record<string, unknown>;
}

function asOptionalText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function asOptionalNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function parseRuntimeContext(
  payload?: Record<string, unknown>,
): RuntimeContext | undefined {
  const raw = asRecord(payload?.runtime) ?? payload;
  if (!raw) return undefined;
  const depth = asOptionalNumber(raw.depth);
  const maxDepth = asOptionalNumber(raw.max_depth);
  const executionProfile = asOptionalText(raw.execution_profile);
  if (depth == null || maxDepth == null || !executionProfile) return undefined;
  const volumeName = asOptionalText(raw.volume_name);
  const executionMode = asOptionalText(raw.execution_mode);
  const sandboxId = asOptionalText(raw.sandbox_id);
  return {
    depth,
    maxDepth,
    executionProfile,
    sandboxActive: raw.sandbox_active === true,
    effectiveMaxIters: asOptionalNumber(raw.effective_max_iters) ?? 10,
    ...(volumeName ? { volumeName } : {}),
    ...(executionMode ? { executionMode } : {}),
    ...(sandboxId ? { sandboxId } : {}),
  };
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

function appendAssistantToken(
  messages: ChatMessage[],
  token: string,
): ChatMessage[] {
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

function completeAssistant(
  messages: ChatMessage[],
  text: string,
): ChatMessage[] {
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

function readGuardrailWarnings(
  payload: Record<string, unknown> | undefined,
): string[] {
  const raw = payload?.guardrail_warnings;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
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
): ChatMessage[] {
  if (text.length === 0) return messages;
  const runtimeContext = parseRuntimeContext(payload);
  return appendTracePart(
    messages,
    {
      kind: "reasoning",
      parts: [{ type: "text", text }],
      isStreaming: false,
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

function trajectoryStepData(
  payload?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  const raw = payload?.step_data;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  return raw as Record<string, unknown>;
}

function normalizeOptionalUnknown(value: unknown): unknown | undefined {
  if (value == null) return undefined;
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed : undefined;
  }
  return value;
}

function parseTrajectoryStepIndex(
  payload?: Record<string, unknown>,
  stepData?: Record<string, unknown>,
): number {
  return (
    asOptionalNumber(payload?.step_index) ??
    asOptionalNumber(stepData?.index) ??
    0
  );
}

function normalizeTrajectoryStep(
  raw: Record<string, unknown>,
  index: number,
  fallbackText?: string,
): NormalizedTrajectoryStep {
  const action = asOptionalText(raw.action);
  const toolName = asOptionalText(raw.tool_name ?? raw.toolName);
  const thought = asOptionalText(raw.thought) ?? asOptionalText(fallbackText);
  const toolInput = normalizeOptionalUnknown(
    raw.tool_args ?? raw.input ?? raw.tool_input ?? raw.toolInput,
  );
  const toolOutput = normalizeOptionalUnknown(
    raw.output ?? raw.observation ?? raw.tool_output ?? raw.toolOutput,
  );
  const label =
    action ||
    (toolName ? `Tool: ${toolName}` : undefined) ||
    (thought ? `Step ${index + 1}` : undefined) ||
    `Step ${index + 1}`;

  return {
    index,
    thought,
    action,
    toolName,
    toolInput,
    toolOutput,
    label,
  };
}

function extractIndexedTrajectorySteps(
  payload?: Record<string, unknown>,
): Map<number, Record<string, unknown>> {
  const stepsByIndex = new Map<number, Record<string, unknown>>();
  if (!payload) return stepsByIndex;

  const pattern =
    /^(thought|tool_name|tool_args|tool_input|input|observation|tool_output|output|action)_(\d+)$/;
  for (const [key, value] of Object.entries(payload)) {
    const match = key.match(pattern);
    if (!match) continue;
    const field = match[1];
    if (!field) continue;
    const index = Number(match[2]);
    if (!Number.isFinite(index)) continue;
    const current = stepsByIndex.get(index) ?? {};
    current[field] = value;
    stepsByIndex.set(index, current);
  }
  return stepsByIndex;
}

function normalizeTrajectorySteps(
  text: string,
  payload?: Record<string, unknown>,
): NormalizedTrajectoryStep[] {
  const stepsByIndex = extractIndexedTrajectorySteps(payload);
  const stepData = trajectoryStepData(payload);

  if (stepData) {
    const index = parseTrajectoryStepIndex(payload, stepData);
    const merged = {
      ...(stepsByIndex.get(index) ?? {}),
      ...stepData,
    };
    stepsByIndex.set(index, merged);
  }

  // Fallback for payloads that expose trajectory fields directly without index.
  if (!stepData && stepsByIndex.size === 0 && payload) {
    const inlineStep: Record<string, unknown> = {};
    for (const field of [
      "thought",
      "action",
      "tool_name",
      "tool_args",
      "tool_input",
      "input",
      "observation",
      "tool_output",
      "output",
    ]) {
      if (payload[field] !== undefined) {
        inlineStep[field] = payload[field];
      }
    }
    if (Object.keys(inlineStep).length > 0) {
      const index = parseTrajectoryStepIndex(payload);
      stepsByIndex.set(index, inlineStep);
    }
  }

  const sorted = [...stepsByIndex.entries()]
    .sort(([left], [right]) => left - right)
    .map(([index, raw], position) =>
      normalizeTrajectoryStep(raw, index, position === 0 ? text : undefined),
    );

  if (sorted.length > 0) {
    return sorted;
  }

  const fallback = text.trim();
  if (!fallback) return [];
  const fallbackIndex = parseTrajectoryStepIndex(payload, stepData);
  return [
    {
      index: fallbackIndex,
      thought: fallback,
      label: `Step ${fallbackIndex + 1}`,
    },
  ];
}

function trajectoryStepDetails(step: NormalizedTrajectoryStep): string[] {
  const details: string[] = [];
  if (step.toolName) {
    details.push(`Tool: ${step.toolName}`);
  }
  if (step.toolInput !== undefined) {
    details.push("Input received");
  }
  if (step.toolOutput !== undefined) {
    details.push("Observation received");
  }
  return details;
}

function upsertChainOfThought(
  messages: ChatMessage[],
  step: NormalizedTrajectoryStep,
): ChatMessage[] {
  const traceStep: ChatTraceStep = {
    id: `trajectory-step-${step.index}`,
    index: step.index,
    label: step.label,
    status: "active",
    details: trajectoryStepDetails(step),
  };

  const idx = latestTraceIndex(
    messages,
    (part) => part.kind === "chain_of_thought",
  );
  if (idx < 0) {
    return appendTracePart(
      messages,
      {
        kind: "chain_of_thought",
        title: "Execution trace",
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
      const leftIndex =
        typeof left.index === "number" ? left.index : Number.POSITIVE_INFINITY;
      const rightIndex =
        typeof right.index === "number"
          ? right.index
          : Number.POSITIVE_INFINITY;
      if (leftIndex !== rightIndex) return leftIndex - rightIndex;
      return left.id.localeCompare(right.id);
    });
    const updatedSteps = sortedSteps.map((candidate) => ({
      ...candidate,
      status:
        candidate.index === step.index
          ? ("active" as const)
          : ("complete" as const),
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

function appendTrajectoryToolPart(
  messages: ChatMessage[],
  step: NormalizedTrajectoryStep,
  parentPayload?: Record<string, unknown>,
): ChatMessage[] {
  if (
    !step.toolName &&
    step.toolInput === undefined &&
    step.toolOutput === undefined
  ) {
    return messages;
  }

  const payload: Record<string, unknown> = {
    step_index: step.index,
    tool_name: step.toolName ?? `trajectory_step_${step.index + 1}`,
  };
  // Forward runtime context from the parent trajectory_step event
  const parentRuntime = asRecord(parentPayload?.runtime);
  if (parentRuntime) {
    payload.runtime = parentRuntime;
  }
  if (step.toolInput !== undefined) {
    payload.tool_input = step.toolInput;
    payload.tool_args = step.toolInput;
    payload.input = step.toolInput;
  }
  if (step.toolOutput !== undefined) {
    payload.tool_output = step.toolOutput;
    payload.output = step.toolOutput;
    payload.observation = step.toolOutput;
  }

  const stateKind: "tool_call" | "tool_result" =
    step.toolOutput !== undefined ? "tool_result" : "tool_call";
  const displayText = step.toolName
    ? `Tool: ${step.toolName}`
    : `Step ${step.index + 1}`;
  return appendToolLikePart(
    messages,
    stateKind,
    displayText,
    payload,
    appendTracePart,
    { traceSource: "trajectory" },
  );
}

function applyTrajectoryStep(
  messages: ChatMessage[],
  step: NormalizedTrajectoryStep,
  parentPayload?: Record<string, unknown>,
  includePrimaryFallback = false,
): ChatMessage[] {
  let next = messages;
  if (includePrimaryFallback && step.thought) {
    next = appendReasoningEvent(next, step.thought, "trajectory");
  }
  if (includePrimaryFallback) {
    next = appendTrajectoryToolPart(next, step, parentPayload);
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

  const includePrimaryFallback = !hasLiveTraceInCurrentTurn(messages);
  return steps.reduce<ChatMessage[]>(
    (acc, step) =>
      applyTrajectoryStep(acc, step, payload, includePrimaryFallback),
    messages,
  );
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
          return part.status === "in_progress"
            ? { ...part, status: "completed" as const }
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
    if (
      changed ||
      msg.id !== messageId ||
      msg.type !== "hitl" ||
      !msg.hitlData
    ) {
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

function rollbackHitlByMessageId(
  messages: ChatMessage[],
  messageId: string,
): ChatMessage[] {
  let changed = false;
  const next = messages.map((msg) => {
    if (
      changed ||
      msg.id !== messageId ||
      msg.type !== "hitl" ||
      !msg.hitlData
    ) {
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
      return {
        messages: appendStatusTrace(
          messages,
          text,
          inferStatusTone(text, payload),
          payload,
        ),
        terminal: false,
        errored: false,
      };
    }
    case "tool_call": {
      return {
        messages: appendToolLikePart(
          messages,
          "tool_call",
          text,
          payload,
          appendTracePart,
        ),
        terminal: false,
        errored: false,
      };
    }
    case "tool_result": {
      return {
        messages: appendToolLikePart(
          messages,
          "tool_result",
          text,
          payload,
          appendTracePart,
        ),
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

      if (queryClient) {
        queryClient.invalidateQueries({ queryKey: ["memory"] });
      }
      return { messages: next, terminal: false, errored: false };
    }
    case "hitl_request": {
      const hitlPayload = asRecord(payload?.hitl ?? payload);
      const question =
        asOptionalText(hitlPayload?.question) ||
        text.trim() ||
        "Approval needed";
      const messageId =
        asOptionalText(hitlPayload?.message_id ?? hitlPayload?.messageId) ??
        nextId("hitl");
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
                variant:
                  variant === "primary" || variant === "secondary"
                    ? variant
                    : "secondary",
              } as const;
            })
            .filter(
              (
                value,
              ): value is { label: string; variant: "primary" | "secondary" } =>
                value != null,
            )
        : [];

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
      const messageId = asOptionalText(
        payload?.message_id ?? payload?.messageId,
      );
      const resolution =
        asOptionalText(payload?.resolution) ??
        asOptionalText(payload?.label) ??
        text.trim();
      if (!resolution) return { messages, terminal: false, errored: false };

      if (messageId) {
        return {
          messages: resolveHitlByMessageId(messages, messageId, resolution),
          terminal: false,
          errored: false,
        };
      }

      let updated = false;
      const next = messages.map((msg) => {
        if (
          updated ||
          msg.type !== "hitl" ||
          !msg.hitlData ||
          msg.hitlData.resolved
        ) {
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
      const resolution =
        asOptionalText(result?.resolution) ??
        asOptionalText(result?.action_label);
      let next = messages;
      if (command === "resolve_hitl" && messageId && resolution) {
        next = resolveHitlByMessageId(next, messageId, resolution);
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
      let next = completeAssistant(messages, text);
      next = finishReasoning(next);
      next = finalizeTraceParts(next);

      const finalReasoning =
        typeof payload?.final_reasoning === "string"
          ? payload.final_reasoning.trim()
          : "";
      if (finalReasoning) {
        next = appendReasoningEvent(
          next,
          `Final reasoning: ${finalReasoning}`,
          "summary",
          payload,
        );
      }

      next = attachFinalReferences(next, payload);

      const warnings = readGuardrailWarnings(payload);
      if (warnings.length > 0) {
        next = appendSystem(
          next,
          `Guardrail warnings:\n- ${warnings.join("\n- ")}`,
        );
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
      next = appendSystem(
        next,
        `Backend error: ${text || "Unknown server error."}`,
      );
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
    const next = finalizeTraceParts(
      appendSystem(messages, `Backend error: ${frame.message}`),
    );
    return { messages: finishReasoning(next), terminal: true, errored: true };
  }
  return applyEvent(messages, frame, queryClient);
}
