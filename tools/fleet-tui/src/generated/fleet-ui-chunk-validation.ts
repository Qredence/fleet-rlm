/**
 * REGENERATED from openapi.yaml by scripts/contracts.py api generate.
 * Do not hand-edit — run `make api-sync`. The dataAlternatives
 * snake_case/camelCase id tolerances are the generator's declared input.
 */

export type FieldCheck = (value: unknown) => boolean;

export const chunkTypes = [
  "turn_start",
  "turn_status",
  "step_start",
  "step_finish",
  "reasoning",
  "code",
  "output",
  "tool_call",
  "tool_result",
  "text",
  "skill",
  "child_progress",
  "attachment",
  "warning",
  "artifact",
  "usage",
  "structured_result",
  "turn_finish",
  "turn_cancelled",
  "turn_error"
] as const;

export const dataFieldChecks: Record<string, Record<string, FieldCheck>> = {
};

export const dataRequiredFields: Record<string, readonly string[]> = {
};

export const dataAlternatives: Record<string, readonly (readonly string[])[]> = {
  "data-status": [["status"], ["detail"], ["message"]],
  "data-skill": [["skill_id"]],
  "data-attachment": [["attachment_id"], ["attachmentId"]],
  "data-artifact": [["artifact_id"]],
  "data-structured-result": [["schema_id", "schema_version"]],
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

