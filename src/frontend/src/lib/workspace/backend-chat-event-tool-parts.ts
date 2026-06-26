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

function mlflowSpanStatus(payload?: Record<string, unknown>): "started" | "completed" | "error" {
  const status = asOptionalText(payload?.status)?.toLowerCase();
  if (status === "error" || status === "failed" || status === "failure") return "error";
  if (
    status === "completed" ||
    status === "complete" ||
    status === "finished" ||
    status === "done"
  ) {
    return "completed";
  }
  return "started";
}

function mlflowSpanState(status: "started" | "completed" | "error"): ChatRenderToolState {
  if (status === "started") return "running";
  if (status === "error") return "output-error";
  return "output-available";
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

export function extractRawToolName(payload?: Record<string, unknown>): string | undefined {
  if (!payload) return undefined;

  const step =
    payload.step && typeof payload.step === "object" && !Array.isArray(payload.step)
      ? (payload.step as Record<string, unknown>)
      : undefined;

  const stepData =
    (payload.step_data ?? payload.stepData) &&
    typeof (payload.step_data ?? payload.stepData) === "object" &&
    !Array.isArray(payload.step_data ?? payload.stepData)
      ? ((payload.step_data ?? payload.stepData) as Record<string, unknown>)
      : undefined;

  const candidates = [
    payload.tool_name,
    payload.toolName,
    payload.name,
    payload.tool,
    step?.tool_name,
    step?.toolName,
    step?.name,
    step?.tool,
    stepData?.tool_name,
    stepData?.toolName,
    stepData?.name,
    stepData?.tool,
  ];

  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  const code = payload.code ?? step?.code;
  if (code && typeof code === "string" && code.trim()) {
    return "repl_execute";
  }

  if (step) {
    const stepType = String(step.type ?? "").toLowerCase();
    if (stepType === "repl") {
      return "repl_execute";
    }
  }

  return undefined;
}

function isSandboxPayload(payload?: Record<string, unknown>): boolean {
  if (!payload) return false;
  const step =
    payload.step && typeof payload.step === "object" && !Array.isArray(payload.step)
      ? (payload.step as Record<string, unknown>)
      : undefined;
  if (step) {
    const stepType = String(step.type ?? "").toLowerCase();
    if (stepType === "repl") return true;
  }
  const rawToolName = extractRawToolName(payload);
  const toolName = String(rawToolName ?? "").toLowerCase();
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
  const argsRecord = asRecord(payload?.tool_args);
  const code =
    asOptionalText(payload?.code_preview) ||
    asOptionalText(payload?.code) ||
    (typeof step?.input === "string" && step.input) ||
    asOptionalText(stepInput?.code) ||
    asOptionalText(stepInput?.code_preview) ||
    asOptionalText(stepInput?.command) ||
    asOptionalText(argsRecord?.code) ||
    asOptionalText(argsRecord?.command) ||
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
  const rawToolName = extractRawToolName(payload);
  return {
    kind: "sandbox",
    title: String(rawToolName ?? "Sandbox"),
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
  const sourceType = asOptionalText(payload?.source_type ?? payload?.sourceType)?.toLowerCase();
  if (sourceType === "mlflow_span" || payload?.event_kind === "mlflow_span") {
    return mlflowSpanFromPayload(text, payload);
  }

  const state = inferToolState(kind, text, payload);
  const stepIndex = asOptionalNumber(payload?.step_index ?? payload?.stepIndex);
  const runtimeContext = parseRuntimeContext(payload);
  const outputValue = payload?.tool_output ?? payload?.output ?? text;
  const rawToolName = extractRawToolName(payload);

  return {
    kind: "tool",
    title: String(rawToolName ?? (text || "Tool")),
    toolType: String(rawToolName ?? "tool"),
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

function mlflowSpanFromPayload(
  text: string,
  payload?: Record<string, unknown>,
): ToolLikeRenderPart {
  const status = mlflowSpanStatus(payload);
  const spanId = asOptionalText(payload?.span_id ?? payload?.spanId);
  const parentSpanId = asOptionalText(payload?.parent_span_id ?? payload?.parentSpanId);
  const traceId = asOptionalText(
    payload?.trace_id ?? payload?.traceId ?? payload?.mlflow_trace_id ?? payload?.mlflowTraceId,
  );
  const durationMs = asOptionalNumber(payload?.duration_ms ?? payload?.durationMs);
  const startedAt = asOptionalText(payload?.started_at ?? payload?.startedAt);
  const endedAt = asOptionalText(payload?.ended_at ?? payload?.endedAt);
  const traceUrl = asOptionalText(payload?.trace_url ?? payload?.traceUrl);
  const experimentId = asOptionalText(payload?.experiment_id ?? payload?.experimentId);
  const trackingUri = asOptionalText(payload?.tracking_uri ?? payload?.trackingUri);
  const spanName =
    asOptionalText(payload?.name ?? payload?.span_name ?? payload?.spanName) ||
    text ||
    "MLflow span";
  const outputValue = payload?.output ?? payload?.span_output ?? payload?.tool_output;
  const errorValue = payload?.error;
  const runtimeContext = parseRuntimeContext(payload);

  return {
    kind: "tool",
    title: spanName,
    toolType: "mlflow_span",
    state: mlflowSpanState(status),
    ...(spanId ? { identityKey: `mlflow_span:${spanId}` } : {}),
    input: payload?.input ?? payload?.span_input ?? payload?.tool_input,
    output:
      status === "started"
        ? undefined
        : (outputValue ?? (status === "error" ? errorValue : undefined)),
    errorText:
      status === "error" ? (stringifyUnknown(errorValue ?? outputValue) ?? text) : undefined,
    mlflowSpan: {
      spanId: spanId ?? "unknown-span",
      status,
      ...(parentSpanId ? { parentSpanId } : {}),
      ...(traceId ? { traceId } : {}),
      ...(durationMs != null ? { durationMs } : {}),
      ...(startedAt ? { startedAt } : {}),
      ...(endedAt ? { endedAt } : {}),
      ...(traceUrl ? { traceUrl } : {}),
      ...(experimentId ? { experimentId } : {}),
      ...(trackingUri ? { trackingUri } : {}),
    },
    ...(runtimeContext ? { runtimeContext } : {}),
  };
}

type ToolLikeRenderPart = Extract<ChatRenderPart, { kind: "tool" | "sandbox" }>;

function isToolLikePart(part: ChatRenderPart): part is ToolLikeRenderPart {
  return part.kind === "tool" || part.kind === "sandbox";
}

function toolIdentity(part: ToolLikeRenderPart): string {
  if (part.kind === "tool" && part.identityKey) return part.identityKey;
  if (part.kind === "tool") return part.toolType || part.title || "tool";
  return part.title || "sandbox";
}

function getBetterCode(existing: string | undefined, incoming: string | undefined): string {
  if (!existing) return incoming || "";
  if (!incoming) return existing;

  const isDictRepr = (s: string) => {
    const trimmed = s.trim();
    return (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) || trimmed.startsWith("Calling tool:")
    );
  };

  if (isDictRepr(existing) && !isDictRepr(incoming)) return incoming;
  if (isDictRepr(incoming) && !isDictRepr(existing)) return existing;

  const existingNewlines = (existing.match(/\n/g) || []).length;
  const incomingNewlines = (incoming.match(/\n/g) || []).length;

  if (existingNewlines > incomingNewlines) return existing;
  if (incomingNewlines > existingNewlines) return incoming;

  return existing.length >= incoming.length ? existing : incoming;
}

function upsertMatchingToolPart(
  messages: ChatMessage[],
  part: ToolLikeRenderPart,
  text: string,
  traceSource: ChatMessage["traceSource"],
): ChatMessage[] | null {
  const incomingIdentity = toolIdentity(part);
  if (part.stepIndex == null && !("identityKey" in part && part.identityKey)) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message || message.type !== "trace" || message.traceSource !== traceSource) continue;
    const renderParts = message.renderParts ?? [];
    for (let j = renderParts.length - 1; j >= 0; j -= 1) {
      const existing = renderParts[j];
      if (!existing || !isToolLikePart(existing)) continue;
      if (existing.kind !== part.kind) continue;
      if (
        "identityKey" in existing &&
        existing.identityKey &&
        "identityKey" in part &&
        part.identityKey
      ) {
        if (existing.identityKey !== part.identityKey) continue;
      } else {
        if (existing.stepIndex !== part.stepIndex) continue;
        if (toolIdentity(existing) !== incomingIdentity) continue;
      }
      const merged: ChatRenderPart =
        existing.kind === "tool" && part.kind === "tool"
          ? {
              ...existing,
              ...part,
              input: existing.input ?? part.input,
              state: part.state ?? existing.state,
              output: part.output ?? existing.output,
              errorText: part.errorText ?? existing.errorText,
              mlflowSpan: part.mlflowSpan
                ? {
                    ...existing.mlflowSpan,
                    ...part.mlflowSpan,
                    spanId: part.mlflowSpan.spanId,
                    status: part.mlflowSpan.status,
                  }
                : existing.mlflowSpan,
            }
          : existing.kind === "sandbox" && part.kind === "sandbox"
            ? {
                ...existing,
                ...part,
                code: getBetterCode(existing.code, part.code),
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
        title: String(payload?.tool_name ?? payload?.toolName ?? "Environment variables"),
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
