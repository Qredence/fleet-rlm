import type { ChatMessage, ChatRenderPart } from "@/lib/workspace/workspace-types";
import { asOptionalText, asRecord } from "@/lib/workspace/backend-chat-event-payload";
import {
  appendToolLikePart,
  inferStatusTone,
  sandboxProgressPartFromStatus,
} from "@/lib/workspace/backend-chat-event-tool-parts";

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

function mlflowSpanPayload(
  payload: Record<string, unknown>,
  text: string,
): Record<string, unknown> {
  const stepObj = asRecord(payload.step);
  const stepInput = asRecord(stepObj?.input);
  const stepOutput = asRecord(stepObj?.output);
  const status =
    asOptionalText(payload.status ?? stepInput?.status ?? stepOutput?.status) ?? "started";
  const name =
    asOptionalText(payload.name ?? payload.span_name ?? stepInput?.span_name ?? stepObj?.label) ||
    text ||
    "MLflow span";

  return {
    ...payload,
    source_type: "mlflow_span",
    event_kind: "mlflow_span",
    tool_name: "mlflow_span",
    span_id: payload.span_id ?? payload.spanId ?? stepInput?.span_id ?? stepInput?.spanId,
    parent_span_id:
      payload.parent_span_id ??
      payload.parentSpanId ??
      stepInput?.parent_span_id ??
      stepInput?.parentSpanId,
    trace_id:
      payload.trace_id ??
      payload.traceId ??
      payload.mlflow_trace_id ??
      payload.mlflowTraceId ??
      stepInput?.trace_id ??
      stepInput?.traceId,
    name,
    status,
    duration_ms:
      payload.duration_ms ??
      payload.durationMs ??
      stepOutput?.duration_ms ??
      stepOutput?.durationMs,
    started_at:
      payload.started_at ?? payload.startedAt ?? stepInput?.started_at ?? stepInput?.startedAt,
    ended_at: payload.ended_at ?? payload.endedAt ?? stepOutput?.ended_at ?? stepOutput?.endedAt,
    trace_url: payload.trace_url ?? payload.traceUrl ?? stepInput?.trace_url ?? stepInput?.traceUrl,
    experiment_id:
      payload.experiment_id ??
      payload.experimentId ??
      stepInput?.experiment_id ??
      stepInput?.experimentId,
    tracking_uri:
      payload.tracking_uri ??
      payload.trackingUri ??
      stepInput?.tracking_uri ??
      stepInput?.trackingUri,
    input: payload.input ?? payload.span_input ?? stepInput?.span_input ?? stepInput,
    output: payload.output ?? payload.span_output ?? stepOutput?.span_output ?? stepOutput,
    error: payload.error ?? stepOutput?.error,
  };
}

function mlflowSpanEventKind(payload: Record<string, unknown>): "tool_call" | "tool_result" {
  const status = asOptionalText(payload.status)?.toLowerCase();
  return status === "completed" || status === "error" ? "tool_result" : "tool_call";
}

function turnInputRowToRenderPart(
  rowKind: string,
  label: string,
  value: string,
  preview?: string,
): ChatRenderPart {
  switch (rowKind) {
    case "user_request":
      return { kind: "request_row", label, value, preview };
    case "active_skills": {
      // Parse skills from value (comma-separated or JSON array)
      let skills: string[] = [];
      try {
        const parsed = JSON.parse(value);
        if (Array.isArray(parsed)) {
          skills = parsed.map((s) => String(s)).filter(Boolean);
        }
      } catch {
        // Fallback: comma-separated list
        skills = value
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
      }
      return { kind: "skills_row", label, skills, preview };
    }
    case "history": {
      // Extract turn count from value or preview
      const countMatch = value.match(/(\d+)\s*turns?/i) || preview?.match(/(\d+)\s*turns?/i);
      const turnCount = countMatch && countMatch[1] ? parseInt(countMatch[1], 10) : 0;
      return { kind: "history_row", label, turnCount, value, preview };
    }
    case "core_memory":
      return { kind: "core_memory_row", label, value, preview };
    case "context":
      return { kind: "context_row", label, value, preview };
    default:
      // Unknown row kind: fall back to generic status-note
      return {
        kind: "status_note",
        text: `${label}: ${preview || value || "(empty)"}`,
        tone: "neutral",
      };
  }
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
    case "mlflow_span": {
      const spanPayload = mlflowSpanPayload(mergedPayload, trimmed);
      return appendToolLikePart(
        messages,
        mlflowSpanEventKind(spanPayload),
        trimmed || asOptionalText(spanPayload.name) || "MLflow span",
        spanPayload,
        deps.appendTracePart,
      );
    }
    case "reasoning": {
      // P2-5: Route RLM reasoning to a compact status trace instead of
      // displaying full internal monologue in the chat. The full reasoning
      // is still available in the trajectory tab.
      const truncated =
        trimmed.length > 200 ? trimmed.slice(0, 200) + "..." : trimmed;
      return deps.appendStatusTrace(
        messages,
        truncated || "Reasoning",
        "neutral",
        { ...mergedPayload, source_type: "rlm_progress" },
      );
    }
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
    case "rlm_progress": {
      // P1-4 + P2-7: Display RLM progress events (rlm_start, rlm_iteration,
      // rlm_action_gen, rlm_complete) as status traces in the chat.
      const status = mergedPayload["status"];
      const tone =
        status === "failed"
          ? "error"
          : status === "completed"
            ? "success"
            : "neutral";
      return deps.appendStatusTrace(
        messages,
        trimmed || "RLM progress",
        tone,
        { ...mergedPayload, source_type: "status" },
      );
    }
    case "clarification":
      return deps.appendClarificationMessage(messages, trimmed, mergedPayload);
    case "sandbox_activity": {
      const category = asOptionalText(mergedPayload.category)?.toLowerCase() ?? "status";
      const tone =
        category === "error"
          ? "error"
          : category === "output" || category === "status"
            ? "neutral"
            : "neutral";
      return deps.appendStatusTrace(
        messages,
        trimmed || "Sandbox activity",
        tone,
        {
          ...mergedPayload,
          source_type: "sandbox_activity",
          category,
        },
      );
    }
    case "turn_inputs": {
      const rows = Array.isArray(mergedPayload.rows) ? mergedPayload.rows : [];
      let nextMessages = messages;
      for (const rawRow of rows) {
        const row = asRecord(rawRow);
        if (!row) continue;
        const rowKind = asOptionalText(row.kind)?.toLowerCase() ?? "";
        const label = asOptionalText(row.label) ?? rowKind;
        const value = asOptionalText(row.value) ?? "";
        const preview = asOptionalText(row.preview);
        const part = turnInputRowToRenderPart(rowKind, label, value, preview);
        nextMessages = deps.appendTracePart(nextMessages, part, value);
      }
      return nextMessages;
    }
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
  const sourceType = sourceTypeFromPayload(payload);
  if (sourceType === "mlflow_span") {
    return routeExecutionStepBySourceType(messages, text, payload, deps);
  }

  const step = asRecord(payload?.step);
  if (!step) {
    return routeExecutionStepBySourceType(messages, text, payload, deps);
  }

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
