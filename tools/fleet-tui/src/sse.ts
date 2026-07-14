import type { components } from "./generated/openapi.js";

export type FleetUIMessageChunk = components["schemas"]["FleetUIMessageChunk"];

const chunkTypes = new Set<FleetUIMessageChunk["type"]>([
  "start",
  "start-step",
  "finish-step",
  "reasoning-start",
  "reasoning-delta",
  "reasoning-end",
  "data-status",
  "data-skill",
  "data-rlm-code",
  "data-rlm-output",
  "tool-input-available",
  "tool-output-available",
  "tool-output-error",
  "data-attachment",
  "data-warning",
  "data-artifact",
  "data-usage",
  "data-structured-result",
  "text-start",
  "text-delta",
  "text-end",
  "finish",
  "abort",
  "error",
]);

export async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let separator = buffer.search(/\r?\n\r?\n/);
      while (separator >= 0) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator).replace(/^\r?\n\r?\n/, "");
        const data = frameData(frame);
        if (data) yield data;
        separator = buffer.search(/\r?\n\r?\n/);
      }
      if (done) {
        const data = frameData(buffer.trim());
        if (data) yield data;
        break;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export function parseUIChunk(data: string): FleetUIMessageChunk | "[DONE]" {
  if (data === "[DONE]") return data;
  const parsed: unknown = JSON.parse(data);
  if (!isFleetUIMessageChunk(parsed)) {
    throw new Error("Fleet API returned an invalid AI SDK UI stream chunk");
  }
  return parsed;
}

function frameData(frame: string): string {
  return frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
}

function isFleetUIMessageChunk(value: unknown): value is FleetUIMessageChunk {
  if (!isRecord(value) || typeof value.type !== "string") return false;
  if (!chunkTypes.has(value.type as FleetUIMessageChunk["type"])) return false;

  switch (value.type) {
    case "start":
      return nonEmptyString(value.messageId) && isRecord(value.messageMetadata);
    case "start-step":
    case "finish-step":
      return true;
    case "reasoning-start":
    case "reasoning-end":
    case "text-start":
    case "text-end":
      return nonEmptyString(value.id);
    case "reasoning-delta":
    case "text-delta":
      return nonEmptyString(value.id) && typeof value.delta === "string";
    case "tool-input-available":
      return nonEmptyString(value.toolCallId) && nonEmptyString(value.toolName) && "input" in value;
    case "tool-output-available":
      return nonEmptyString(value.toolCallId) && "output" in value;
    case "tool-output-error":
      return nonEmptyString(value.toolCallId) && nonEmptyString(value.errorText);
    case "finish":
      return value.finishReason === "stop" || value.finishReason === "error";
    case "abort":
      return typeof value.reason === "string";
    case "error":
      return nonEmptyString(value.errorText);
    case "data-status":
    case "data-skill":
    case "data-rlm-code":
    case "data-rlm-output":
    case "data-attachment":
    case "data-warning":
    case "data-artifact":
    case "data-usage":
    case "data-structured-result":
      return "data" in value;
    default:
      return false;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}
