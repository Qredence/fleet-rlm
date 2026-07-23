import type { Message } from "./store.js";

export type ObservedTokenCounts = {
  input: number | null;
  output: number | null;
};

const inputKeys = ["prompt_tokens", "promptTokens", "input_tokens", "inputTokens"] as const;
const outputKeys = [
  "completion_tokens",
  "completionTokens",
  "output_tokens",
  "outputTokens",
] as const;

/** Normalize DSPy's model-keyed observed usage without inventing missing telemetry. */
export function observedTokenCounts(value: Record<string, unknown>): ObservedTokenCounts {
  return {
    input: sumObservedMetric(value, inputKeys),
    output: sumObservedMetric(value, outputKeys),
  };
}

/** Sum successful committed Turn observations currently present in the conversation. */
export function committedTokenCounts(messages: readonly Message[]): ObservedTokenCounts {
  let input = 0;
  let output = 0;
  let sawInput = false;
  let sawOutput = false;

  for (const message of messages) {
    if (message.kind !== "usage") continue;
    if (message.inputTokens !== null) {
      input += message.inputTokens;
      sawInput = true;
    }
    if (message.outputTokens !== null) {
      output += message.outputTokens;
      sawOutput = true;
    }
  }

  return {
    input: sawInput ? input : null,
    output: sawOutput ? output : null,
  };
}

function sumObservedMetric(
  usageByModel: Record<string, unknown>,
  keys: readonly string[],
): number | null {
  let total = 0;
  let observed = false;

  for (const rawEntry of Object.values(usageByModel)) {
    const entry = record(rawEntry);
    for (const key of keys) {
      const value = nonnegativeNumber(entry[key]);
      if (value === null) continue;
      total += value;
      observed = true;
      break;
    }
  }

  return observed ? total : null;
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function nonnegativeNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return value;
}
