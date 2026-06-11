import type {
  ChatEnvVarItem,
  ChatMessage,
  ChatRenderPart,
  ChatRenderToolState,
} from "@/lib/workspace/workspace-types";
import {
  asOptionalNumber,
  asOptionalText,
  asRecord,
  parseRuntimeContext,
  stringifyUnknown,
} from "@/lib/workspace/backend-chat-event-payload";

function hasExplicitErrorValue(record: Record<string, unknown>): boolean {
  for (const key of ["error", "error_text", "errorText", "stderr"]) {
    const value = record[key];
    if (value == null) continue;
    if (typeof value === "string") {
      if (value.trim()) return true;
      continue;
    }
    return true;
  }
  return false;
}

function payloadLooksErrored(payload?: Record<string, unknown>): boolean {
  if (!payload) return false;

  const directStatus = asOptionalText(payload.status)?.toLowerCase();
  if (
    directStatus &&
    ["error", "failed", "failure", "rejected", "cancelled"].includes(directStatus)
  ) {
    return true;
  }
  if (payload.success === false || payload.ok === false || payload.failed === true) {
    return true;
  }

  const objectCandidates = [
    asRecord(payload.tool_output),
    asRecord(payload.output),
    asRecord(payload.observation),
    asRecord(payload.result),
  ];
  for (const candidate of objectCandidates) {
    if (!candidate) continue;
    const status = asOptionalText(candidate.status)?.toLowerCase();
    if (status && ["error", "failed", "failure", "rejected", "cancelled"].includes(status)) {
      return true;
    }
    if (candidate.success === false || candidate.ok === false || candidate.failed === true) {
      return true;
    }
    if (hasExplicitErrorValue(candidate)) {
      return true;
    }
  }

  if (hasExplicitErrorValue(payload)) {
    return true;
  }

  return false;
}

function payloadLooksSuccessful(payload?: Record<string, unknown>): boolean {
  if (!payload) return false;

  const directStatus = asOptionalText(payload.status)?.toLowerCase();
  if (directStatus && ["ok", "success", "completed", "finished", "done"].includes(directStatus)) {
    return true;
  }
  if (payload.success === true || payload.ok === true) {
    return true;
  }

  const objectCandidates = [
    asRecord(payload.tool_output),
    asRecord(payload.output),
    asRecord(payload.observation),
    asRecord(payload.result),
  ];
  for (const candidate of objectCandidates) {
    if (!candidate) continue;
    const status = asOptionalText(candidate.status)?.toLowerCase();
    if (status && ["ok", "success", "completed", "finished", "done"].includes(status)) {
      return true;
    }
    if (candidate.success === true || candidate.ok === true) {
      return true;
    }
  }

  return false;
}

function textLooksErrored(text: string): boolean {
  const normalized = text.trim().toLowerCase();
  if (!normalized) return false;
  if (
    normalized.startsWith("execution error") ||
    normalized.startsWith("tool result: execution error") ||
    normalized.startsWith("tool result: error") ||
    normalized.startsWith("tool result: failed") ||
    normalized.startsWith("error:") ||
    normalized.startsWith("failed:") ||
    normalized.startsWith("exception") ||
    normalized.startsWith("traceback")
  ) {
    return true;
  }
  return (
    normalized.length <= 160 && /\b(error|failed|failure|rejected|cancelled)\b/.test(normalized)
  );
}

export function inferToolState(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatRenderToolState {
  if (kind === "tool_call") return "running";
  if (payloadLooksErrored(payload)) return "output-error";
  if (payloadLooksSuccessful(payload)) return "output-available";
  return textLooksErrored(text) ? "output-error" : "output-available";
}

export function inferStatusTone(
  text: string,
  payload?: Record<string, unknown>,
): Extract<ChatRenderPart, { kind: "status_note" }>["tone"] {
  if (
    payloadLooksErrored(payload) ||
    (!payloadLooksSuccessful(payload) && textLooksErrored(text))
  ) {
    return "error";
  }
  if (payloadLooksSuccessful(payload)) {
    return "success";
  }
  if (/warn|warning|caution/i.test(text)) {
    return "warning";
  }
  if (/done|complete|completed|finished|success/i.test(text)) {
    return "success";
  }
  return "neutral";
}

function parseEnvVariablesFromPayload(payload?: Record<string, unknown>): ChatEnvVarItem[] | null {
  if (!payload) return null;

  const objectCandidates: unknown[] = [
    payload.env,
    payload.variables,
    payload.tool_output,
    payload.output,
  ];

  for (const candidate of objectCandidates) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      continue;
    }
    const entries = Object.entries(candidate as Record<string, unknown>).filter(
      ([k, v]) =>
        /^[A-Z0-9_]+$/.test(k) &&
        (typeof v === "string" || typeof v === "number" || typeof v === "boolean"),
    );
    if (entries.length === 0) continue;
    return entries.slice(0, 50).map(([name, value]) => ({
      name,
      value: String(value),
    }));
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
        const match = line.match(/^([A-Z0-9_]+)=(.*)$/);
        if (!match) return null;
        return { name: match[1], value: match[2] };
      })
      .filter((value): value is ChatEnvVarItem => value != null);
    if (rows.length > 0) return rows.slice(0, 50);
  }

  return null;
}

function isSandboxPayload(payload?: Record<string, unknown>): boolean {
  if (!payload) return false;
  const step = payload.step;
  if (step && typeof step === "object" && !Array.isArray(step)) {
    const stepType = String((step as Record<string, unknown>).type ?? "").toLowerCase();
    if (stepType === "repl") return true;
  }
  const toolName = String(payload.tool_name ?? "").toLowerCase();
  return ["python", "repl", "shell", "exec", "interpreter"].some((s) => toolName.includes(s));
}

function sandboxFromPayload(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ToolLikeRenderPart {
  const step =
    payload?.step && typeof payload.step === "object" && !Array.isArray(payload.step)
      ? (payload.step as Record<string, unknown>)
      : undefined;
  const stepInput = asRecord(step?.input);
  const stepOutput = asRecord(step?.output);
  const code =
    (typeof step?.input === "string" && step.input) ||
    asOptionalText(stepInput?.code) ||
    asOptionalText(stepInput?.code_preview) ||
    asOptionalText(stepInput?.command) ||
    (typeof payload?.tool_input === "string" && payload.tool_input) ||
    (typeof payload?.tool_args === "string" && payload.tool_args) ||
    "";
  const output =
    (typeof step?.output === "string" && step.output) ||
    asOptionalText(stepOutput?.stdout) ||
    asOptionalText(stepOutput?.stderr) ||
    asOptionalText(stepOutput?.output) ||
    asOptionalText(stepOutput?.result) ||
    (typeof payload?.tool_output === "string" && payload.tool_output) ||
    text;
  const state = inferToolState(kind, text, payload);
  const stepIndex = asOptionalNumber(payload?.step_index ?? payload?.stepIndex);
  const runtimeContext = parseRuntimeContext(payload);
  return {
    kind: "sandbox",
    title: String(payload?.tool_name ?? "Sandbox"),
    state,
    stepIndex,
    code,
    output,
    errorText: state === "output-error" ? (stringifyUnknown(output) ?? text) : undefined,
    language: "text",
    ...(runtimeContext ? { runtimeContext } : {}),
  };
}

export function sandboxProgressPartFromStatus(
  payload?: Record<string, unknown>,
): ChatRenderPart | null {
  if (!payload) return null;
  const streamText = asOptionalText(
    payload.stream_text ?? payload.streamText ?? payload.stdout_preview,
  );
  if (!streamText) return null;
  const phase = asOptionalText(payload.phase);
  if (phase !== "sandbox_output") return null;

  const streamName = asOptionalText(payload.stream)?.toLowerCase() ?? "stdout";
  const stepIndex = asOptionalNumber(payload.iteration ?? payload.step_index ?? payload.stepIndex);
  const runtimeContext = parseRuntimeContext(payload);
  const isErrorStream = streamName === "stderr";

  return {
    kind: "sandbox",
    title: `Sandbox ${streamName}`,
    state: isErrorStream ? "output-error" : "running",
    stepIndex,
    output: streamText,
    errorText: isErrorStream ? streamText : undefined,
    language: "text",
    ...(runtimeContext ? { runtimeContext } : {}),
  };
}

function toolFromPayload(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ToolLikeRenderPart {
  const state = inferToolState(kind, text, payload);
  const stepIndex = asOptionalNumber(payload?.step_index ?? payload?.stepIndex);
  const runtimeContext = parseRuntimeContext(payload);
  const outputValue = payload?.tool_output ?? payload?.output ?? text;
  return {
    kind: "tool",
    title: String(payload?.tool_name ?? (text || "Tool")),
    toolType: String(payload?.tool_name ?? "tool"),
    state,
    stepIndex,
    input: payload?.tool_input ?? payload?.tool_args ?? payload?.input,
    output: outputValue,
    errorText:
      state === "output-error"
        ? (stringifyUnknown(outputValue) ?? text ?? "Tool error")
        : undefined,
    ...(runtimeContext ? { runtimeContext } : {}),
  };
}

type ToolLikeRenderPart = Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>;

function isToolLikePart(part: ChatRenderPart): part is ToolLikeRenderPart {
  return part.kind === "tool" || part.kind === "sandbox";
}

function toolIdentity(part: ToolLikeRenderPart): string {
  if (part.kind === "tool") return part.toolType || part.title || "tool";
  return part.title || "sandbox";
}

function upsertMatchingToolPart(
  messages: ChatMessage[],
  part: ToolLikeRenderPart,
  text: string,
  traceSource: ChatMessage["traceSource"],
): ChatMessage[] | null {
  if (part.stepIndex == null) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message || message.type !== "trace" || message.traceSource !== traceSource) continue;
    const renderParts = message.renderParts ?? [];
    for (let j = renderParts.length - 1; j >= 0; j -= 1) {
      const existing = renderParts[j];
      if (!existing || !isToolLikePart(existing)) continue;
      if (existing.kind !== part.kind) continue;
      if (existing.stepIndex !== part.stepIndex) continue;
      if (toolIdentity(existing) !== toolIdentity(part)) continue;
      const merged: ChatRenderPart =
        existing.kind === "tool" && part.kind === "tool"
          ? {
              ...existing,
              ...part,
              input: existing.input ?? part.input,
              state: part.state ?? existing.state,
              output: part.output ?? existing.output,
              errorText: part.errorText ?? existing.errorText,
            }
          : existing.kind === "sandbox" && part.kind === "sandbox"
            ? {
                ...existing,
                ...part,
                state: part.state ?? existing.state,
                output: part.output ?? existing.output,
                errorText: part.errorText ?? existing.errorText,
              }
            : part;
      const copy = [...messages];
      const nextParts = [...renderParts];
      nextParts[j] = merged;
      copy[i] = {
        ...message,
        content: text || message.content,
        renderParts: nextParts,
      };
      return copy;
    }
    break;
  }
  return null;
}

export function appendToolLikePart(
  messages: ChatMessage[],
  kind: "tool_call" | "tool_result",
  text: string,
  payload: Record<string, unknown> | undefined,
  appendTracePart: (
    messages: ChatMessage[],
    part: ChatRenderPart,
    content?: string,
    traceSource?: ChatMessage["traceSource"],
  ) => ChatMessage[],
  options?: { traceSource?: ChatMessage["traceSource"] },
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
      options?.traceSource ?? "live",
    );
  }

  const part = isSandboxPayload(payload)
    ? sandboxFromPayload(kind, text, payload)
    : toolFromPayload(kind, text, payload);

  const traceSource = options?.traceSource ?? "live";
  const upserted = upsertMatchingToolPart(messages, part, text, traceSource);
  if (upserted) return upserted;

  return appendTracePart(messages, part, text, traceSource);
}
