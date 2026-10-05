import type { components } from "./generated/openapi.js";
import { chunkTypes as chunkTypeList, isRecord } from "./generated/fleet-ui-chunk-validation.js";

/**
 * The AI SDK UI chunk contract is owned in TWO hand-edited places plus one
 * generated consumer; the golden stream test (`tests/stream-fixture.test.ts`)
 * locks all of them:
 *
 * 1. Typed transport models    — src/fleet_rlm/api/ui_stream.py
 * 2. Backend runtime projector — src/fleet_rlm/api/sse.py (AISDKUIProjector)
 * 3. Backend reload projection — src/fleet_rlm/api/ui_message.py
 * 4. This runtime validator    — tables REGENERATED from openapi.yaml by
 *    scripts/contracts.py api generate (imported below)
 *
 * The validator is the STRICTEST consumer: a backend emission that violates
 * it throws mid-stream ("Fleet API returned an invalid AI SDK UI stream
 * chunk"), so a shape change must land in #1/#3 together, after which
 * `make api-sync` refreshes the generated tables.
 */
export type ModernFleetUIChunk = components["schemas"]["FleetUIMessageChunk"];

const legacyChunkTypes = [
  "start",
  "start-step",
  "finish-step",
  "reasoning-start",
  "reasoning-delta",
  "reasoning-end",
  "text-start",
  "text-delta",
  "text-end",
  "tool-input-available",
  "tool-output-available",
  "tool-output-error",
  "data-status",
  "data-child-progress",
  "data-skill",
  "data-rlm-code",
  "data-rlm-output",
  "data-attachment",
  "data-warning",
  "data-artifact",
  "data-usage",
  "data-structured-result",
  "finish",
  "abort",
  "error",
] as const;

export type LegacyStartChunk = {
  type: "start";
  messageId: string;
  messageMetadata?: Record<string, unknown>;
};
export type LegacyStepChunk = {
  type: "start-step" | "finish-step";
};
export type LegacyReasoningStartChunk = { type: "reasoning-start"; id: string };
export type LegacyReasoningDeltaChunk = { type: "reasoning-delta"; id: string; delta: string };
export type LegacyReasoningEndChunk = { type: "reasoning-end"; id: string };
export type LegacyTextStartChunk = { type: "text-start"; id: string };
export type LegacyTextDeltaChunk = { type: "text-delta"; id: string; delta: string };
export type LegacyTextEndChunk = { type: "text-end"; id: string };
export type LegacyToolInputChunk = {
  type: "tool-input-available";
  toolCallId: string;
  toolName: string;
  input?: unknown;
};
export type LegacyToolOutputChunk = {
  type: "tool-output-available";
  toolCallId: string;
  output?: unknown;
};
export type LegacyToolErrorChunk = {
  type: "tool-output-error";
  toolCallId: string;
  errorText: string;
};
export type LegacyFinishChunk = {
  type: "finish";
  finishReason: string;
  messageMetadata?: Record<string, unknown>;
};
export type LegacyAbortChunk = {
  type: "abort";
  reason: string;
};
export type LegacyErrorChunk = {
  type: "error";
  errorText: string;
};
export type LegacyDataChunk = {
  type:
    | "data-status"
    | "data-child-progress"
    | "data-skill"
    | "data-rlm-code"
    | "data-rlm-output"
    | "data-attachment"
    | "data-warning"
    | "data-artifact"
    | "data-usage"
    | "data-structured-result";
  id?: string;
  data: Record<string, any>;
  [key: string]: unknown;
};
export type LegacyFleetUIChunk =
  | LegacyStartChunk
  | LegacyStepChunk
  | LegacyReasoningStartChunk
  | LegacyReasoningDeltaChunk
  | LegacyReasoningEndChunk
  | LegacyTextStartChunk
  | LegacyTextDeltaChunk
  | LegacyTextEndChunk
  | LegacyToolInputChunk
  | LegacyToolOutputChunk
  | LegacyToolErrorChunk
  | LegacyFinishChunk
  | LegacyAbortChunk
  | LegacyErrorChunk
  | LegacyDataChunk;

export type FleetUIMessageChunk = ModernFleetUIChunk | LegacyFleetUIChunk;

const chunkTypes = new Set<string>([...chunkTypeList, ...legacyChunkTypes]);

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
    case "turn_start":
      return nonEmptyString(value.runId) || nonEmptyString(value.run_id);
    case "turn_status":
      return typeof value.phase === "string";
    case "step_start":
    case "step_finish":
      return typeof value.step === "number";
    case "reasoning":
      return (
        (nonEmptyString(value.streamId) || nonEmptyString(value.stream_id)) &&
        typeof value.final === "boolean"
      );
    case "code":
      return (
        (nonEmptyString(value.streamId) || nonEmptyString(value.stream_id)) &&
        typeof value.code === "string"
      );
    case "output":
      return (
        (nonEmptyString(value.streamId) || nonEmptyString(value.stream_id)) &&
        typeof value.output === "string"
      );
    case "tool_call":
      return (
        (nonEmptyString(value.toolCallId) || nonEmptyString(value.tool_call_id)) &&
        (nonEmptyString(value.toolName) || nonEmptyString(value.tool_name))
      );
    case "tool_result":
      return nonEmptyString(value.toolCallId) || nonEmptyString(value.tool_call_id);
    case "text":
      return typeof value.delta === "string" || typeof value.text === "string";
    case "skill":
      return nonEmptyString(value.skillId) || nonEmptyString(value.skill_id);
    case "child_progress":
      return (
        (nonEmptyString(value.childId) || nonEmptyString(value.child_id)) &&
        (nonEmptyString(value.taskLabel) || nonEmptyString(value.task_label))
      );
    case "attachment":
      return nonEmptyString(value.attachmentId) || nonEmptyString(value.attachment_id);
    case "warning":
      return typeof value.message === "string";
    case "artifact":
      return nonEmptyString(value.artifactId) || nonEmptyString(value.artifact_id);
    case "usage":
      return typeof value.iterations === "number";
    case "structured_result":
      return nonEmptyString(value.schemaId) || nonEmptyString(value.schema_id);
    case "turn_finish":
      return typeof value.finishReason === "string" || typeof value.finish_reason === "string";
    case "turn_cancelled":
      return typeof value.reason === "string";
    case "turn_error":
      return typeof value.message === "string";
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
    case "data-child-progress":
    case "data-skill":
    case "data-rlm-code":
    case "data-rlm-output":
    case "data-attachment":
    case "data-warning":
    case "data-artifact":
    case "data-usage":
    case "data-structured-result":
      return isLegacyDataPayload(value.type, value.data);
    default:
      return true;
  }
}

function isLegacyDataPayload(type: string, data: unknown): boolean {
  if (!isRecord(data)) return false;
  switch (type) {
    case "data-status":
      return (
        typeof data.phase === "string" &&
        (typeof data.status === "string" ||
          typeof data.detail === "string" ||
          (typeof data.message === "string" && data.message.length > 0))
      );
    case "data-skill":
      return (
        typeof data.skill_id === "string" &&
        typeof data.name === "string" &&
        typeof data.version === "string"
      );
    case "data-rlm-code":
      return typeof data.code === "string";
    case "data-rlm-output":
      return typeof data.output === "string";
    case "data-attachment":
      return typeof data.attachment_id === "string" && typeof data.filename === "string";
    case "data-warning":
      return typeof data.message === "string";
    case "data-artifact":
      return typeof data.artifact_id === "string";
    case "data-usage":
      return isRecord(data.usage);
    case "data-structured-result":
      return (
        typeof data.schema_id === "string" &&
        typeof data.schema_version === "string" &&
        "value" in data
      );
    default:
      return true;
  }
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}
