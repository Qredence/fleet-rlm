import type { WsEventKind, WsServerEvent, WsServerMessage } from "@/lib/rlm-api/ws-types";

function isWsEventKind(value: string): value is WsEventKind {
  return [
    "assistant_token",
    "reasoning_step",
    "status",
    "warning",
    "tool_call",
    "tool_result",
    "trajectory_step",
    "final",
    "error",
    "cancelled",
    "plan_update",
    "rlm_executing",
    "memory_update",
    "hitl_request",
    "hitl_resolved",
    "command_ack",
    "command_reject",
  ].includes(value);
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (!value) return "";

  if (typeof value === "object" && !Array.isArray(value)) {
    const rec = value as Record<string, unknown>;
    const extracted = rec.text ?? rec.output ?? rec.result ?? rec.message;
    if (typeof extracted === "string") return extracted;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function asNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function asTimestamp(value: unknown): string | number | undefined {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return undefined;
}

function normalizeExecutionStepKind(step: Record<string, unknown>): WsEventKind {
  const rawType = String(step.type ?? "")
    .trim()
    .toLowerCase();

  if (rawType === "output") return "final";
  if (rawType === "tool" || rawType === "repl") {
    return step.output == null ? "tool_call" : "tool_result";
  }
  if (rawType === "memory") return "status";
  if (rawType === "llm") {
    return typeof step.output === "string" && step.output.length > 0
      ? "assistant_token"
      : "reasoning_step";
  }

  return "status";
}

function parseExecutionEnvelope(parsed: Record<string, unknown>): WsServerEvent | null {
  const frameType = String(parsed.type ?? "").trim();
  if (!frameType.startsWith("execution_")) return null;

  if (frameType === "execution_started") {
    return {
      type: "event",
      data: {
        kind: "status",
        text: asText(parsed.message ?? "Execution started"),
        payload: {
          source_type: frameType,
          ...parsed,
        },
        timestamp: asTimestamp(parsed.timestamp),
      },
    };
  }

  if (frameType === "execution_completed") {
    const summary = asRecord(parsed.summary) ?? asRecord(parsed.payload);
    const artifact = asRecord(summary?.final_artifact ?? summary?.finalArtifact);
    const artifactValue = asRecord(artifact?.value);
    return {
      type: "event",
      data: {
        kind: "final",
        text: asText(
          parsed.output ??
            artifactValue?.final_markdown ??
            artifactValue?.summary ??
            artifactValue?.text ??
            artifactValue?.content ??
            artifact?.value ??
            summary?.output ??
            parsed.result ??
            parsed.message,
        ),
        payload: {
          source_type: frameType,
          ...(summary ? { run_summary: summary, ...summary } : {}),
          raw: parsed,
        },
        timestamp: asTimestamp(parsed.timestamp),
      },
    };
  }

  if (frameType !== "execution_step") {
    return null;
  }

  const nested = asRecord(parsed.data);
  const step = asRecord(parsed.step) ?? asRecord(nested?.step);
  if (!step) {
    return {
      type: "event",
      data: {
        kind: "status",
        text: "Execution step received",
        payload: {
          source_type: frameType,
          raw: parsed,
        },
        timestamp: asTimestamp(parsed.timestamp),
      },
    };
  }

  const kind = normalizeExecutionStepKind(step);
  const text = asText(
    step.label ?? step.output ?? step.input ?? step.content ?? step.message ?? kind,
  );

  return {
    type: "event",
    data: {
      kind,
      text,
      payload: {
        source_type: frameType,
        step,
        raw: parsed,
      },
      timestamp: asTimestamp(step.timestamp) ?? asTimestamp(parsed.timestamp),
    },
  };
}

export function parseWsServerFrame(parsed: Record<string, unknown>): WsServerMessage | null {
  const frameType = String(parsed.type ?? "");

  if (frameType === "event") {
    const data = asRecord(parsed.data);
    const envelope = data ?? parsed;

    const kind = String(envelope.kind ?? "");
    if (!isWsEventKind(kind)) return null;

    return {
      type: "event",
      data: {
        kind,
        text: asText(envelope.text),
        payload: asRecord(envelope.payload) ?? undefined,
        timestamp: asTimestamp(envelope.timestamp) ?? asTimestamp(parsed.timestamp),
        version: asNumber(envelope.version ?? parsed.version),
        event_id:
          typeof envelope.event_id === "string"
            ? envelope.event_id
            : typeof parsed.event_id === "string"
              ? parsed.event_id
              : undefined,
      },
    };
  }

  if (frameType === "command_result") {
    const result = asRecord(parsed.result) ?? {};
    const command = asText(parsed.command || "command");
    const status = String(result.status ?? "ok").toLowerCase();
    const kind: WsEventKind = status === "ok" ? "command_ack" : "command_reject";

    return {
      type: "event",
      data: {
        kind,
        text:
          kind === "command_ack"
            ? `${command} completed`
            : asText(result.error ?? `${command} failed`),
        payload: {
          command,
          result,
          raw: parsed,
        },
        version: asNumber(parsed.version),
        event_id: typeof parsed.event_id === "string" ? parsed.event_id : undefined,
      },
    };
  }

  const executionEnvelope = parseExecutionEnvelope(parsed);
  if (executionEnvelope) return executionEnvelope;

  if (frameType === "error") {
    return {
      type: "error",
      message: asText(parsed.message || "WebSocket server error"),
    };
  }

  return null;
}

export function createWsError(detail: string): Error {
  const error = new Error(detail);
  error.name = "RlmWebSocketError";
  return error;
}
