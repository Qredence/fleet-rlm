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
    status: isNullableString,
    detail: isNullableString,
    message: isNullableString,
  },
  "data-skill": {
    skill_id: isString,
    name: isString,
    version: isString,
    phase: isNullableString,
    trust: isNullableString,
    affordances: isNullableStringArray,
    skillId: isNullableString,
  },
  "data-rlm-code": {
    code: isString,
    step: isNullableInteger,
    stream_id: isNullableString,
    is_delta: isNullableBoolean,
    is_final: isNullableBoolean,
  },
  "data-rlm-output": {
    output: isString,
    step: isNullableInteger,
    stream_id: isNullableString,
    is_delta: isNullableBoolean,
    is_final: isNullableBoolean,
  },
  "data-attachment": {
    attachment_id: isString,
    filename: isString,
    phase: isNullableString,
    byte_size: isNullableInteger,
    attachmentId: isNullableString,
    byteSize: isNullableInteger,
  },
  "data-warning": {
    message: isString,
    code: isNullableString,
  },
  "data-artifact": {
    artifact_id: isString,
    artifact_kind: isNullableString,
    kind: isNullableString,
    title: isNullableString,
    name: isNullableString,
    media_type: isNullableString,
    mediaType: isNullableString,
    byte_size: isNullableInteger,
    byteSize: isNullableInteger,
    checksum_sha256: isNullableString,
    checksumSha256: isNullableString,
    artifactId: isNullableString,
  },
  "data-usage": {
    usage: isRecord,
  },
  "data-structured-result": {
    schema_id: isString,
    schema_version: isString,
    schemaId: isNullableString,
    schemaVersion: isNullableString,
  },
};

export const dataRequiredFields: Record<string, readonly string[]> = {
  "data-status": ["phase"],
  "data-skill": ["skill_id", "name", "version"],
  "data-rlm-code": ["code"],
  "data-rlm-output": ["output"],
  "data-attachment": ["attachment_id", "filename"],
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

function isNullableBoolean(value: unknown): boolean {
  return value === null || isBoolean(value);
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

function isNullableStringArray(value: unknown): boolean {
  return value === null || isStringArray(value);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

