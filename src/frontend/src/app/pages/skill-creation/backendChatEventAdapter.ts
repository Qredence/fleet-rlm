import type {
  ChatEnvVarItem,
  ChatInlineCitation,
  ChatMessage,
  ChatQueueItem,
  ChatRenderPart,
  ChatRenderToolState,
  ChatTraceStep,
} from "@/lib/data/types";
import type { WsServerEvent, WsServerMessage } from "@/lib/rlm-api";
import { createLocalId } from "@/lib/id";
import { QueryClient } from "@tanstack/react-query";

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

function latestOpenReasoningIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg?.type === "reasoning" && msg.reasoningData?.isThinking) return i;
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

type ReasoningAppendMode = "line" | "chunk";

function mergeReasoningParts(
  existingParts: { type: "text"; text: string }[],
  text: string,
  mode: ReasoningAppendMode,
): { type: "text"; text: string }[] {
  if (existingParts.length === 0) {
    return [{ type: "text", text }];
  }

  if (mode === "line") {
    return [...existingParts, { type: "text", text }];
  }

  const nextParts = [...existingParts];
  const last = nextParts[nextParts.length - 1];
  if (!last) return [{ type: "text", text }];

  const incoming = text;
  const lastText = last.text;

  const startsNewStructuredLine =
    /^(status:|tool call:|tool result:|plan:|warning:|error:)/i.test(incoming);
  const looksLikeSentenceChunk =
    incoming.length <= 32 ||
    /^[a-z0-9(,[\]'"`]/.test(incoming) ||
    /^[)\].,;:!?]/.test(incoming);
  const shouldJoin =
    !startsNewStructuredLine &&
    (looksLikeSentenceChunk || !/[.!?:]\s*$/.test(lastText));

  if (shouldJoin) {
    const needsSpace =
      !/\s$/.test(lastText) &&
      !/^[)\].,;:!?]/.test(incoming) &&
      !/^['"`]/.test(incoming);
    nextParts[nextParts.length - 1] = {
      type: "text",
      text: `${lastText}${needsSpace ? " " : ""}${incoming}`,
    };
    return nextParts;
  }

  nextParts.push({ type: "text", text: incoming });
  return nextParts;
}

function appendReasoning(
  messages: ChatMessage[],
  text: string,
  mode: ReasoningAppendMode = "line",
): ChatMessage[] {
  const trimmed = text.trim();
  if (!trimmed) return messages;

  const idx = latestOpenReasoningIndex(messages);
  if (idx >= 0) {
    const msg = messages[idx];
    if (!msg?.reasoningData) return messages;
    const parts = mergeReasoningParts(msg.reasoningData.parts, trimmed, mode);

    const copy = [...messages];
    copy[idx] = upsertReasoningRenderPart(
      {
        ...msg,
        reasoningData: {
          ...msg.reasoningData,
          parts,
          isThinking: true,
        },
      },
      parts,
      true,
      msg.reasoningData.duration,
    );
    return copy;
  }

  const parts = mergeReasoningParts([], trimmed, mode);
  return [
    ...messages,
    upsertReasoningRenderPart(
      {
        id: nextId("reasoning"),
        type: "reasoning",
        content: "",
        phase: DEFAULT_PHASE,
        reasoningData: {
          parts,
          isThinking: true,
        },
      },
      parts,
      true,
    ),
  ];
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
): ChatMessage[] {
  return [
    ...messages,
    {
      id: nextId("trace"),
      type: "trace",
      content,
      phase: DEFAULT_PHASE,
      renderParts: [part],
    },
  ];
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
    );
  }

  const copy = [...messages];
  const msg = copy[idx];
  if (!msg?.renderParts?.length) return messages;
  const nextParts = msg.renderParts.map((part) => {
    if (part.kind !== "queue") return part;
    return { ...part, items: [...part.items, queueItem] };
  });
  copy[idx] = { ...msg, content: label, renderParts: nextParts };
  return copy;
}

function upsertChainOfThought(
  messages: ChatMessage[],
  text: string,
  payload?: Record<string, unknown>,
): ChatMessage[] {
  const stepData =
    payload?.step_data &&
    typeof payload.step_data === "object" &&
    !Array.isArray(payload.step_data)
      ? (payload.step_data as Record<string, unknown>)
      : undefined;

  const stepIndex =
    typeof payload?.step_index === "number" ? payload.step_index : undefined;
  const label =
    (typeof stepData?.thought === "string" && stepData.thought.trim()) ||
    (typeof stepData?.action === "string" && stepData.action.trim()) ||
    text.trim() ||
    (stepIndex != null ? `Step ${stepIndex + 1}` : "Trace step");

  const details: string[] = [];
  if (typeof stepData?.tool_name === "string" && stepData.tool_name.trim()) {
    details.push(`Tool: ${stepData.tool_name}`);
  }
  if (typeof stepData?.input === "string" && stepData.input.trim()) {
    details.push(`Input: ${stepData.input}`);
  }
  if (
    typeof stepData?.observation === "string" &&
    stepData.observation.trim()
  ) {
    details.push(`Observation: ${stepData.observation}`);
  }
  if (typeof stepData?.output === "string" && stepData.output.trim()) {
    details.push(`Output: ${stepData.output}`);
  }

  const step: ChatTraceStep = {
    id: nextId("trace-step"),
    label,
    status: "active",
    details,
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
        steps: [step],
      },
      label,
    );
  }

  const copy = [...messages];
  const msg = copy[idx];
  if (!msg?.renderParts) return messages;
  const nextParts = msg.renderParts.map((part) => {
    if (part.kind !== "chain_of_thought") return part;
    const completedSteps = part.steps.map((s) =>
      s.status === "active" ? { ...s, status: "complete" as const } : s,
    );
    return { ...part, steps: [...completedSteps, step] };
  });
  copy[idx] = { ...msg, content: label, renderParts: nextParts };
  return copy;
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

function inferToolState(
  kind: "tool_call" | "tool_result",
  text: string,
): ChatRenderToolState {
  if (kind === "tool_call") return "running";
  return /error|failed/i.test(text) ? "output-error" : "output-available";
}

function parseEnvVariablesFromPayload(
  payload?: Record<string, unknown>,
): ChatEnvVarItem[] | null {
  if (!payload) return null;

  const objectCandidates: unknown[] = [
    payload.env,
    payload.variables,
    payload.tool_output,
    payload.output,
  ];

  for (const candidate of objectCandidates) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate))
      continue;
    const entries = Object.entries(candidate as Record<string, unknown>).filter(
      ([k, v]) =>
        /^[A-Z0-9_]+$/.test(k) &&
        (typeof v === "string" ||
          typeof v === "number" ||
          typeof v === "boolean"),
    );
    if (entries.length === 0) continue;
    return entries
      .slice(0, 50)
      .map(([name, value]) => ({ name, value: String(value) }));
  }

  const strCandidates: unknown[] = [
    payload.tool_output,
    payload.output,
    payload.tool_input,
    payload.tool_args,
  ];
  for (const candidate of strCandidates) {
    if (typeof candidate !== "string") continue;
    const rows = candidate
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
        if (!m) return null;
        return { name: m[1], value: m[2] };
      })
      .filter((v): v is ChatEnvVarItem => v != null);
    if (rows.length > 0) return rows.slice(0, 50);
  }

  return null;
}

function isSandboxPayload(payload?: Record<string, unknown>): boolean {
  if (!payload) return false;
  const step = payload.step;
  if (step && typeof step === "object" && !Array.isArray(step)) {
    const stepType = String(
      (step as Record<string, unknown>).type ?? "",
    ).toLowerCase();
    if (stepType === "repl") return true;
  }
  const toolName = String(payload.tool_name ?? "").toLowerCase();
  return ["python", "repl", "shell", "exec", "interpreter"].some((s) =>
    toolName.includes(s),
  );
}

function sandboxFromPayload(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatRenderPart {
  const step =
    payload?.step &&
    typeof payload.step === "object" &&
    !Array.isArray(payload.step)
      ? (payload.step as Record<string, unknown>)
      : undefined;
  const code =
    (typeof step?.input === "string" && step.input) ||
    (typeof payload?.tool_input === "string" && payload.tool_input) ||
    (typeof payload?.tool_args === "string" && payload.tool_args) ||
    "";
  const output =
    (typeof step?.output === "string" && step.output) ||
    (typeof payload?.tool_output === "string" && payload.tool_output) ||
    text;
  const state = inferToolState(kind, text);
  return {
    kind: "sandbox",
    title: String(payload?.tool_name ?? "Sandbox"),
    state,
    code,
    output,
    errorText: state === "output-error" ? output : undefined,
    language: "text",
  };
}

function toolFromPayload(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatRenderPart {
  const state = inferToolState(kind, text);
  return {
    kind: "tool",
    title: String(payload?.tool_name ?? (text || "Tool")),
    toolType: String(payload?.tool_name ?? "tool"),
    state,
    input: payload?.tool_input ?? payload?.tool_args ?? payload?.input,
    output: payload?.tool_output ?? payload?.output ?? text,
    errorText: state === "output-error" ? text || "Tool error" : undefined,
  };
}

function upsertToolLikePart(
  messages: ChatMessage[],
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatMessage[] {
  const envVars = parseEnvVariablesFromPayload(payload);
  if (envVars && kind === "tool_result") {
    return appendTracePart(
      messages,
      {
        kind: "environment_variables",
        title: String(payload?.tool_name ?? "Environment variables"),
        variables: envVars,
      },
      text,
    );
  }

  const part = isSandboxPayload(payload)
    ? sandboxFromPayload(kind, text, payload)
    : toolFromPayload(kind, text, payload);

  if (kind === "tool_result") {
    const toolName = String(payload?.tool_name ?? "");
    const idx = latestTraceIndex(messages, (existing) => {
      if (existing.kind !== part.kind) return false;
      if (existing.kind === "tool") {
        const open =
          existing.state === "running" || existing.state === "input-streaming";
        return (
          open &&
          (!toolName ||
            existing.toolType === toolName ||
            existing.title === toolName)
        );
      }
      if (existing.kind === "sandbox") {
        const open =
          existing.state === "running" || existing.state === "input-streaming";
        return open && (!toolName || existing.title === toolName);
      }
      return false;
    });

    if (idx >= 0) {
      const copy = [...messages];
      const msg = copy[idx];
      if (msg?.renderParts) {
        copy[idx] = {
          ...msg,
          content: text || msg.content,
          renderParts: msg.renderParts.map((rp) => {
            if (rp.kind !== part.kind) return rp;
            if (rp.kind === "tool" && part.kind === "tool") {
              const open =
                rp.state === "running" || rp.state === "input-streaming";
              const nameMatches =
                !toolName || rp.toolType === toolName || rp.title === toolName;
              if (!open || !nameMatches) return rp;
              return {
                ...rp,
                state: part.state,
                output: part.output,
                errorText: part.errorText,
              };
            }
            if (rp.kind === "sandbox" && part.kind === "sandbox") {
              const open =
                rp.state === "running" || rp.state === "input-streaming";
              const nameMatches = !toolName || rp.title === toolName;
              if (!open || !nameMatches) return rp;
              return {
                ...rp,
                state: part.state,
                output: part.output,
                errorText: part.errorText,
                code: part.code || rp.code,
              };
            }
            return rp;
          }),
        };
        return copy;
      }
    }
  }

  return appendTracePart(messages, part, text);
}

function attachInlineCitations(
  messages: ChatMessage[],
  payload?: Record<string, unknown>,
): ChatMessage[] {
  const raw = payload?.citations;
  if (!Array.isArray(raw) || raw.length === 0) return messages;
  const citations = raw
    .map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return null;
      const rec = item as Record<string, unknown>;
      const title = typeof rec.title === "string" ? rec.title : "Source";
      const url = typeof rec.url === "string" ? rec.url : "";
      if (!url) return null;
      return {
        number: typeof rec.number === "string" ? rec.number : undefined,
        title,
        url,
        description:
          typeof rec.description === "string" ? rec.description : undefined,
        quote: typeof rec.quote === "string" ? rec.quote : undefined,
      };
    })
    .filter(
      (v): v is NonNullable<typeof v> => v != null,
    ) satisfies ChatInlineCitation[];

  if (citations.length === 0) return messages;

  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (!msg || msg.type !== "assistant") continue;
    const copy = [...messages];
    copy[i] = {
      ...msg,
      renderParts: [
        ...(msg.renderParts ?? []),
        { kind: "inline_citation_group", citations },
      ],
    };
    return copy;
  }
  return messages;
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
        messages: appendReasoning(messages, text, "chunk"),
        terminal: false,
        errored: false,
      };
    }
    case "trajectory_step": {
      return {
        messages: upsertChainOfThought(messages, text, payload),
        terminal: false,
        errored: false,
      };
    }
    case "status": {
      return {
        messages: appendReasoning(messages, `Status: ${text}`, "line"),
        terminal: false,
        errored: false,
      };
    }
    case "tool_call": {
      return {
        messages: upsertToolLikePart(messages, "tool_call", text, payload),
        terminal: false,
        errored: false,
      };
    }
    case "tool_result": {
      return {
        messages: upsertToolLikePart(messages, "tool_result", text, payload),
        terminal: false,
        errored: false,
      };
    }
    case "plan_update": {
      let next = finishReasoning(messages);
      next = upsertQueue(next, text || "Running plan...");
      return { messages: next, terminal: false, errored: false };
    }
    case "rlm_executing": {
      let next = finishReasoning(messages);
      const toolName =
        typeof payload?.tool_name === "string" && payload.tool_name
          ? payload.tool_name
          : "Sub-agent iteration";
      next = appendTracePart(
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
      let next = finishReasoning(messages);
      next = appendTracePart(
        next,
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
    case "final": {
      let next = completeAssistant(messages, text);
      next = finishReasoning(next);
      next = finalizeTraceParts(next);

      const finalReasoning =
        typeof payload?.final_reasoning === "string"
          ? payload.final_reasoning.trim()
          : "";
      if (finalReasoning) {
        next = appendReasoning(next, `Final reasoning: ${finalReasoning}`);
        next = finishReasoning(next);
      }

      next = attachInlineCitations(next, payload);

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
