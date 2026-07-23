import type { Message } from "./store.js";

export type ExecutionSummary = {
  iterations: number | null;
  subLmCalls: number | null;
  hostCapabilityCalls: number | null;
  interpreterErrors: number | null;
  durationMs: number | null;
};

const UNKNOWN_SUMMARY: ExecutionSummary = {
  iterations: null,
  subLmCalls: null,
  hostCapabilityCalls: null,
  interpreterErrors: null,
  durationMs: null,
};

/** Derive complete per-Run execution telemetry from existing projected messages. */
export function summarizeExecution(
  messages: readonly Message[],
  runId: string | null,
): ExecutionSummary {
  if (!runId) return { ...UNKNOWN_SUMMARY };
  let usage: Extract<Message, { kind: "usage" }> | undefined;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.kind === "usage" && message.runId === runId) {
      usage = message;
      break;
    }
  }
  if (!usage) return { ...UNKNOWN_SUMMARY };

  const toolCalls = new Map<string, Extract<Message, { kind: "tool" }>>();
  const failedSteps = new Set<number>();
  for (const message of messages) {
    if (!("runId" in message) || message.runId !== runId) continue;
    if (message.kind === "tool") toolCalls.set(message.toolCallId, message);
    if (message.kind === "output" && isInterpreterError(message.output)) {
      failedSteps.add(message.step);
    }
  }

  let subLmCalls = 0;
  let hostCapabilityCalls = 0;
  for (const message of toolCalls.values()) {
    if (message.name === "llm_query") {
      subLmCalls += 1;
    } else if (message.name === "llm_query_batched") {
      const promptCount = record(message.input).prompt_count;
      if (!Number.isSafeInteger(promptCount) || (promptCount as number) < 0) {
        subLmCalls = Number.NaN;
      } else if (!Number.isNaN(subLmCalls)) {
        subLmCalls += promptCount as number;
      }
    } else if (message.name.toUpperCase() !== "SUBMIT") {
      hostCapabilityCalls += 1;
    }
  }
  const executionDetailsComplete = !messages.some(
    (message) =>
      message.kind === "warning" &&
      message.runId === runId &&
      message.message === "some detailed execution events were omitted",
  );
  const unobservedSemanticCall = messages.some(
    (message) =>
      message.kind === "code" &&
      message.runId === runId &&
      /\bllm_query(?:_batched)?\s*\(/.test(message.code) &&
      ![...toolCalls.values()].some(
        (tool) => tool.name === "llm_query" || tool.name === "llm_query_batched",
      ),
  );

  return {
    iterations: nonnegativeFinite(usage.iterations),
    subLmCalls:
      executionDetailsComplete && !unobservedSemanticCall && !Number.isNaN(subLmCalls)
        ? subLmCalls
        : null,
    hostCapabilityCalls: executionDetailsComplete ? hostCapabilityCalls : null,
    interpreterErrors: executionDetailsComplete ? failedSteps.size : null,
    durationMs: nonnegativeFinite(usage.durationMs),
  };
}

export function formatExecutionMetric(value: number | null): string {
  return value === null ? "—" : String(value);
}

function isInterpreterError(output: string): boolean {
  return /^\s*(?:\[Error\]|Execution (?:error|failed)\b)/i.test(output);
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function nonnegativeFinite(value: number | null): number | null {
  return value !== null && Number.isFinite(value) && value >= 0 ? value : null;
}
