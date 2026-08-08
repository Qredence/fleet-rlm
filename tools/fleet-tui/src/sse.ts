import type { components } from "./generated/openapi.js";
import {
  chunkTypes as chunkTypeList,
  dataAlternatives,
  dataFieldChecks,
  dataRequiredFields,
  isRecord,
} from "./generated/fleet-ui-chunk-validation.js";

/**
 * The AI SDK UI chunk contract is owned in TWO hand-edited places plus one
 * generated consumer; the golden stream test (`tests/stream-fixture.test.ts`)
 * locks all of them:
 *
 * 1. Backend runtime projector  — src/fleet_rlm/api/sse.py (AISDKUIProjector)
 * 2. Backend reload projection  — src/fleet_rlm/api/ui_message.py
 * 3. OpenAPI hook               — src/fleet_rlm/api/openapi.py (_CHUNK_FIELD_*)
 * 4. This runtime validator     — tables REGENERATED from openapi.yaml by
 *    scripts/generate_tui_chunk_validation.py (imported below)
 *
 * The validator is the STRICTEST consumer: a backend emission that violates
 * it throws mid-stream ("Fleet API returned an invalid AI SDK UI stream
 * chunk"), so a shape change must land in #1/#3 together, after which
 * `make api-sync` refreshes the generated tables.
 */
export type FleetUIMessageChunk = components["schemas"]["FleetUIMessageChunk"];

const chunkTypes = new Set<FleetUIMessageChunk["type"]>(chunkTypeList);

export async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;

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
        completed = true;
        break;
      }
    }
  } finally {
    if (!completed) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function parseUIChunk(data: string): FleetUIMessageChunk | "[DONE]" {
  const payload = data.trim();
  if (payload === "[DONE]") return payload;
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch {
    throw new Error("Fleet API returned an invalid AI SDK UI stream chunk");
  }
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
      return isTypedDataPayload(value.type, value.data);
    default:
      return false;
  }
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isTypedDataPayload(type: string, value: unknown): boolean {
  if (!isRecord(value)) return false;
  const checks = dataFieldChecks[type];
  if (!checks) return false;
  if (!(dataRequiredFields[type] ?? []).every((field) => hasOwn(value, field))) return false;
  const alternatives = dataAlternatives[type] ?? [];
  if (
    alternatives.length &&
    !alternatives.some((group) => group.every((field) => hasUsableValue(value, field)))
  ) {
    return false;
  }
  return Object.entries(checks).every(
    ([field, check]) => !hasOwn(value, field) || check(value[field]),
  );
}

function hasOwn(value: Record<string, unknown>, field: string): boolean {
  return Object.hasOwn(value, field);
}

function hasUsableValue(value: Record<string, unknown>, field: string): boolean {
  return hasOwn(value, field) && value[field] !== null && value[field] !== undefined;
}
