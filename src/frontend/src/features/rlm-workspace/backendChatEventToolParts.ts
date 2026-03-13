import type {
  ChatEnvVarItem,
  ChatMessage,
  ChatRenderPart,
  ChatRenderToolState,
} from "@/lib/data/types";
import {
  asOptionalNumber,
  asOptionalText,
  asRecord,
  parseRuntimeContext,
  stringifyUnknown,
} from "@/features/rlm-workspace/backendChatEventPayload";

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
    ["error", "failed", "failure", "rejected", "cancelled"].includes(
      directStatus,
    )
  ) {
    return true;
  }
  if (
    payload.success === false ||
    payload.ok === false ||
    payload.failed === true
  ) {
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
    if (
      status &&
      ["error", "failed", "failure", "rejected", "cancelled"].includes(status)
    ) {
      return true;
    }
    if (
      candidate.success === false ||
      candidate.ok === false ||
      candidate.failed === true
    ) {
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

export function inferToolState(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatRenderToolState {
  if (kind === "tool_call") return "running";
  return payloadLooksErrored(payload) || /error|failed/i.test(text)
    ? "output-error"
    : "output-available";
}

export function inferStatusTone(
  text: string,
  payload?: Record<string, unknown>,
): Extract<ChatRenderPart, { kind: "status_note" }>["tone"] {
  if (payloadLooksErrored(payload) || /error|failed|failure/i.test(text)) {
    return "error";
  }
  if (/warn|warning|caution/i.test(text)) {
    return "warning";
  }
  if (/done|complete|completed|finished|success/i.test(text)) {
    return "success";
  }
  return "neutral";
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
    if (
      !candidate ||
      typeof candidate !== "object" ||
      Array.isArray(candidate)
    ) {
      continue;
    }
    const entries = Object.entries(candidate as Record<string, unknown>).filter(
      ([k, v]) =>
        /^[A-Z0-9_]+$/.test(k) &&
        (typeof v === "string" ||
          typeof v === "number" ||
          typeof v === "boolean"),
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
    errorText:
      state === "output-error" ? (stringifyUnknown(output) ?? text) : undefined,
    language: "text",
    ...(runtimeContext ? { runtimeContext } : {}),
  };
}

function toolFromPayload(
  kind: "tool_call" | "tool_result",
  text: string,
  payload?: Record<string, unknown>,
): ChatRenderPart {
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

  return appendTracePart(messages, part, text, options?.traceSource ?? "live");
}
