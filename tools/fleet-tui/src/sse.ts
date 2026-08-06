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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

type FieldCheck = (value: unknown) => boolean;

const dataFieldChecks: Record<string, Record<string, FieldCheck>> = {
  "data-status": {
    phase: isString,
    status: isString,
    detail: isString,
    message: isNullableString,
  },
  "data-skill": {
    skill_id: isString,
    skillId: isString,
    name: isString,
    version: isString,
    phase: (value) => value === "activated" || value === "loaded",
    trust: isString,
    affordances: isStringArray,
  },
  "data-rlm-code": {
    code: isString,
    step: isNullableInteger,
    stream_id: isNullableString,
    is_delta: isBoolean,
    is_final: isBoolean,
  },
  "data-rlm-output": {
    output: isString,
    step: isNullableInteger,
    stream_id: isNullableString,
    is_delta: isBoolean,
    is_final: isBoolean,
  },
  "data-attachment": {
    attachment_id: isString,
    attachmentId: isString,
    phase: isString,
    filename: isString,
    byte_size: isInteger,
    byteSize: isInteger,
  },
  "data-warning": { message: isString, code: isNullableString },
  "data-artifact": {
    artifact_id: isString,
    artifactId: isString,
    artifact_kind: isString,
    kind: isString,
    title: isNullableString,
    name: isString,
    media_type: isString,
    mediaType: isString,
    byte_size: isInteger,
    byteSize: isInteger,
    checksum_sha256: isString,
    checksumSha256: isString,
  },
  "data-usage": { usage: isRecord },
  "data-structured-result": {
    schema_id: isString,
    schemaId: isString,
    schema_version: isString,
    schemaVersion: isString,
  },
};

const dataRequiredFields: Record<string, readonly string[]> = {
  "data-status": ["phase"],
  "data-skill": ["name", "version"],
  "data-rlm-code": ["code"],
  "data-rlm-output": ["output"],
  "data-attachment": ["filename"],
  "data-warning": ["message"],
  "data-artifact": [],
  "data-usage": ["usage"],
  "data-structured-result": ["value"],
};

const dataAlternatives: Record<string, readonly (readonly string[])[]> = {
  "data-status": [["status"], ["detail"], ["message"]],
  "data-skill": [["skill_id"], ["skillId"]],
  "data-attachment": [["attachment_id"], ["attachmentId"]],
  "data-artifact": [["artifact_id"], ["artifactId"]],
  "data-structured-result": [
    ["schema_id", "schema_version"],
    ["schemaId", "schemaVersion"],
  ],
};

function isTypedDataPayload(type: string, value: unknown): boolean {
  if (!isRecord(value)) return false;
  const checks = dataFieldChecks[type];
  if (!checks) return false;
  if (!(dataRequiredFields[type] ?? []).every((field) => hasOwn(value, field))) return false;
  const alternatives = dataAlternatives[type] ?? [];
  if (
    alternatives.length &&
    !alternatives.some((group) => group.every((field) => hasOwn(value, field)))
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

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNullableString(value: unknown): boolean {
  return value === null || isString(value);
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}

function isNullableInteger(value: unknown): boolean {
  return value === null || isInteger(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isString);
}
