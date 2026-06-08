import type { ChatMessage } from "@/lib/workspace/workspace-types";
import { asOptionalText, asRecord } from "@/lib/workspace/backend-chat-event-payload";
import { appendToolLikePart, inferStatusTone, sandboxProgressPartFromStatus } from "@/lib/workspace/backend-chat-event-tool-parts";

export type TracePartAppender = (
  messages: ChatMessage[],
  part: import("@/lib/workspace/workspace-types").ChatRenderPart,
  content?: string,
  traceSource?: ChatMessage["traceSource"],
) => ChatMessage[];

export type ExecutionStepRouterDeps = {
  appendTracePart: TracePartAppender;
  appendOrExtendReasoningEvent: (
    messages: ChatMessage[],
    text: string,
    traceSource: ChatMessage["traceSource"],
    payload?: Record<string, unknown>,
    label?: string,
  ) => ChatMessage[];
  appendAssistantToken: (messages: ChatMessage[], token: string) => ChatMessage[];
  appendStatusTrace: (
    messages: ChatMessage[],
    text: string,
    tone: "neutral" | "success" | "warning" | "error",
    payload?: Record<string, unknown>,
    traceSource?: ChatMessage["traceSource"],
  ) => ChatMessage[];
  appendClarificationMessage: (
    messages: ChatMessage[],
    text: string,
    payload?: Record<string, unknown>,
  ) => ChatMessage[];
};

export function sourceTypeFromPayload(payload?: Record<string, unknown>): string {
  return asOptionalText(payload?.source_type ?? payload?.sourceType)?.toLowerCase() ?? "";
}

function canonicalStepText(step: Record<string, unknown>, fallback: string): string {
  return (
    asOptionalText(step.label) ??
    asOptionalText(step.output) ??
    asOptionalText(step.input) ??
    fallback
  );
}

function stepInputKind(step: Record<string, unknown>): string {
  const input = asRecord(step.input);
  return (
    asOptionalText(input?.phase)?.toLowerCase() ??
    asOptionalText(input?.event_kind)?.toLowerCase() ??
    ""
  );
}

function routingStatusText(text: string, payload?: Record<string, unknown>): string {
  const selectedSkills = Array.isArray(payload?.selected_skills)
    ? payload.selected_skills.map((item) => String(item)).filter(Boolean)
    : [];
  const routingDecision = asOptionalText(payload?.routing_decision);
  const sourceUrl = asOptionalText(payload?.source_url);
  if (selectedSkills.length === 0 && !routingDecision && !sourceUrl) return text;

  const parts = [text.trim()].filter(Boolean);
  if (routingDecision) parts.push(`route ${routingDecision}`);
  if (selectedSkills.length > 0) parts.push(`skills ${selectedSkills.join(", ")}`);
  if (sourceUrl) parts.push(`source ${sourceUrl}`);
  return parts.join(" | ");
}

export function routeExecutionStepBySourceType(
  messages: ChatMessage[],
  text: string,
  payload: Record<string, unknown> | undefined,
  deps: ExecutionStepRouterDeps,
): ChatMessage[] {
  const sourceType = sourceTypeFromPayload(payload);
  const trimmed = text.trim();
  const mergedPayload = payload ?? {};

  switch (sourceType) {
    case "reasoning":
      return trimmed
        ? deps.appendOrExtendReasoningEvent(messages, trimmed, "live", mergedPayload)
        : messages;
    case "text":
      return trimmed ? deps.appendAssistantToken(messages, trimmed) : messages;
    case "tool_call":
      return appendToolLikePart(
        messages,
        "tool_call",
        trimmed || asOptionalText(mergedPayload["tool_name"]) || "tool_call",
        mergedPayload,
        deps.appendTracePart,
      );
    case "tool_result":
      return appendToolLikePart(
        messages,
        "tool_result",
        trimmed || asOptionalText(mergedPayload["tool_name"]) || "tool_result",
        mergedPayload,
        deps.appendTracePart,
      );
    case "warning":
      return deps.appendStatusTrace(messages, trimmed || "Warning", "warning", mergedPayload);
    case "status":
    case "turn_started": {
      const sandboxPart = sandboxProgressPartFromStatus(mergedPayload);
      if (sandboxPart) {
        return deps.appendTracePart(messages, sandboxPart, trimmed);
      }
      return deps.appendStatusTrace(
        messages,
        trimmed || "Status update",
        inferStatusTone(trimmed, mergedPayload) ?? "neutral",
        mergedPayload,
      );
    }
    case "sandbox_exec":
      return appendToolLikePart(
        messages,
        mergedPayload["tool_output"] == null ? "tool_call" : "tool_result",
        trimmed || asOptionalText(mergedPayload["tool_name"]) || "repl",
        {
          ...mergedPayload,
          tool_name: asOptionalText(mergedPayload["tool_name"]) ?? "repl",
          step: {
            type: "repl",
            label: trimmed || "sandbox_exec",
            input: mergedPayload["tool_input"] ?? mergedPayload["tool_args"],
            output: mergedPayload["tool_output"] ?? mergedPayload["output"],
          },
        },
        deps.appendTracePart,
      );
    case "rlm_delegate":
      return appendToolLikePart(
        messages,
        mergedPayload["tool_output"] == null ? "tool_call" : "tool_result",
        trimmed || "delegate_to_rlm",
        {
          ...mergedPayload,
          tool_name: asOptionalText(mergedPayload["tool_name"]) ?? "delegate_to_rlm",
        },
        deps.appendTracePart,
      );
    case "clarification":
      return deps.appendClarificationMessage(messages, trimmed, mergedPayload);
    default:
      return deps.appendStatusTrace(
        messages,
        routingStatusText(trimmed || "Execution step received", mergedPayload),
        "neutral",
        mergedPayload,
      );
  }
}

export function applyCanonicalExecutionStepWithRouter(
  messages: ChatMessage[],
  text: string,
  payload: Record<string, unknown> | undefined,
  deps: ExecutionStepRouterDeps,
): ChatMessage[] {
  const step = asRecord(payload?.step);
  if (!step) {
    return routeExecutionStepBySourceType(messages, text, payload, deps);
  }

  const sourceType = sourceTypeFromPayload(payload);
  const stepType = asOptionalText(step.type)?.toLowerCase();
  const stepText = canonicalStepText(step, text);

  if (stepType === "tool" || stepType === "repl") {
    const kind = step.output == null ? "tool_call" : "tool_result";
    return appendToolLikePart(
      messages,
      kind,
      stepText,
      { ...payload, ...step },
      deps.appendTracePart,
    );
  }

  if (stepType === "llm") {
    const inputKind = stepInputKind(step);
    const output = asRecord(step.output);
    const token = typeof output?.text === "string" ? output.text : asOptionalText(step.output);
    const isReasoning = sourceType === "reasoning" || inputKind === "reasoning";
    const isStatus = sourceType === "status" || inputKind === "status";

    if (isReasoning) {
      const reasoningText = token || stepText;
      return reasoningText
        ? deps.appendOrExtendReasoningEvent(messages, reasoningText, "live", {
            ...payload,
            ...step,
          })
        : messages;
    }

    if (isStatus) {
      const sandboxPart = sandboxProgressPartFromStatus({
        ...payload,
        ...step,
        phase: asOptionalText(payload?.phase) ?? asOptionalText(asRecord(step.input)?.phase),
      });
      if (sandboxPart) {
        return deps.appendTracePart(messages, sandboxPart, stepText);
      }
      return deps.appendStatusTrace(
        messages,
        stepText,
        inferStatusTone(stepText, { ...payload, ...step }) ?? "neutral",
        { ...payload, ...step },
      );
    }

    // Assistant-labeled tokens and unlabeled token fallbacks both stream as reply text.
    if (token) {
      return deps.appendAssistantToken(messages, token);
    }

    return stepText
      ? deps.appendOrExtendReasoningEvent(messages, stepText, "live", { ...payload, ...step })
      : messages;
  }

  if (stepType === "output") {
    const label = asOptionalText(step.label)?.toLowerCase() ?? "";
    if (label === "assistant_output") {
      return messages;
    }
    return deps.appendStatusTrace(
      messages,
      stepText || "Output step completed",
      "success",
      payload,
    );
  }

  return deps.appendStatusTrace(
    messages,
    routingStatusText(stepText || "Execution step received", payload),
    "neutral",
    payload,
  );
}
