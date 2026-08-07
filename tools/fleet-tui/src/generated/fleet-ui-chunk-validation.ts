/**
 * REGENERATED from openapi.yaml by scripts/generate_tui_chunk_validation.py.
 * Do not hand-edit — run `make api-sync`. The dataAlternatives dual
 * snake_case/camelCase id tolerances are the generator's declared input.
 */

export type FieldCheck = (value: unknown) => boolean;

export const chunkTypes = [
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
  "error"
] as const;

export const dataFieldChecks: Record<string, Record<string, FieldCheck>> = {
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
  "data-warning": {
    message: isString,
    code: isNullableString,
  },
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
  "data-usage": {
    usage: isRecord,
  },
  "data-structured-result": {
    schema_id: isString,
    schemaId: isString,
    schema_version: isString,
    schemaVersion: isString,
  },
};

export const dataRequiredFields: Record<string, readonly string[]> = {
  "data-status": ["phase"],
  "data-skill": ["name", "version", "skill_id"],
  "data-rlm-code": ["code"],
  "data-rlm-output": ["output"],
  "data-attachment": ["filename", "attachment_id"],
  "data-warning": ["message"],
  "data-artifact": ["artifact_id"],
  "data-usage": ["usage"],
  "data-structured-result": ["schema_id", "schema_version", "value"],
};

export const dataAlternatives: Record<string, readonly (readonly string[])[]> = {
  "data-status": [["status"], ["detail"], ["message"]],
  "data-skill": [["skill_id"], ["skillId"]],
  "data-attachment": [["attachment_id"], ["attachmentId"]],
  "data-artifact": [["artifact_id"], ["artifactId"]],
  "data-structured-result": [["schema_id", "schema_version"], ["schemaId", "schemaVersion"]],
};

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

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

