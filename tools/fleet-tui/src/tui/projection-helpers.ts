import type {
  ArtifactEvent,
  AttachmentEvent,
  SkillEvent,
  UsageEvent,
  WarningEvent,
} from "./canonical.js";
import type { Message, Role } from "./store.js";
import { observedTokenCounts } from "./usage-summary.js";

export type Clock = () => number;

export function text(
  id: string,
  role: Role,
  value: string,
  streaming: boolean,
  clock: Clock,
  runId?: string,
): Message {
  return {
    id,
    kind: "text",
    role,
    text: value,
    streaming,
    ...(runId ? { runId } : {}),
    ts: clock(),
  };
}

export function thinking(
  id: string,
  runId: string,
  step: number,
  value: string,
  clock: Clock,
): Message {
  return { id, kind: "reasoning", runId, step, text: value, ts: clock() };
}

export function tool(
  id: string,
  runId: string,
  toolCallId: string,
  name: string,
  input: unknown,
  output: unknown,
  error: string | undefined,
  clock: Clock,
  startedAt = clock(),
): Message {
  const ended = output !== undefined || error !== undefined;
  let status: "error" | "success" | "running" = "running";
  if (error !== undefined) {
    status = "error";
  } else if (output !== undefined) {
    status = "success";
  }
  return {
    id,
    kind: "tool",
    runId,
    toolCallId,
    name,
    input,
    ...(output !== undefined ? { output } : {}),
    ...(error !== undefined ? { error } : {}),
    startedAt,
    ...(ended ? { endedAt: clock() } : {}),
    status,
    ts: clock(),
  };
}

export function code(
  id: string,
  runId: string,
  step: number,
  value: string,
  streaming: boolean,
  clock: Clock,
): Message {
  return {
    id,
    kind: "code",
    runId,
    step,
    code: value,
    language: "python",
    streaming,
    ts: clock(),
  };
}

export function output(
  id: string,
  runId: string,
  step: number,
  value: string,
  streaming: boolean,
  clock: Clock,
): Message {
  return {
    id,
    kind: "output",
    runId,
    step,
    output: value,
    streaming,
    ts: clock(),
  };
}

export function result(
  id: string,
  runId: string,
  schemaId: string,
  schemaVersion: string,
  value: unknown,
  narrative: string | undefined,
  clock: Clock,
): Message {
  return {
    id,
    kind: "result",
    runId,
    schemaId,
    schemaVersion,
    value,
    ...(narrative ? { narrative } : {}),
    ts: clock(),
  };
}

export function skill(id: string, runId: string, event: SkillEvent, clock: Clock): Message {
  return {
    id,
    kind: "skill",
    runId,
    skillId: event.skillId || "(skill)",
    name: event.name ?? "(skill)",
    phase: event.phase === "activated" || event.trust !== undefined ? "activated" : "loaded",
    version: event.version ?? "1.0.0",
    ...(event.trust ? { trust: event.trust } : {}),
    ...(event.affordances?.length ? { affordances: event.affordances } : {}),
    ts: clock(),
  };
}

export function attachment(
  id: string,
  runId: string,
  event: AttachmentEvent,
  clock: Clock,
): Message {
  return {
    id,
    kind: "attachment",
    runId,
    attachmentId: event.attachmentId || "(attachment)",
    filename: event.filename ?? "(file)",
    bytes: event.byteSize ?? 0,
    ts: clock(),
  };
}

export function warning(id: string, runId: string, event: WarningEvent, clock: Clock): Message {
  return {
    id,
    kind: "warning",
    runId,
    code: event.code || "warning",
    message: event.message,
    ts: clock(),
  };
}

export function artifact(id: string, runId: string, event: ArtifactEvent, clock: Clock): Message {
  return {
    id,
    kind: "artifact",
    runId,
    artifactId: event.artifactId || "(artifact)",
    name: event.title ?? "(artifact)",
    artifactKind: event.artifactKind ?? "file",
    bytes: event.byteSize ?? 0,
    ts: clock(),
  };
}

export function usage(id: string, runId: string, event: UsageEvent, clock: Clock): Message {
  const value = data(event.usage);
  const observedLmUsage = data(value.observed_lm_usage);
  const tokens = observedTokenCounts(observedLmUsage);
  return {
    id,
    kind: "usage",
    runId,
    iterations: nullableNumber(value.iterations),
    inputTokens: tokens.input,
    outputTokens: tokens.output,
    durationMs: nullableNumber(value.duration_ms),
    observedLmUsage,
    ts: clock(),
  };
}

export function normalizedNarrative(narrative: string, value: unknown): string | undefined {
  const trimmed = narrative.trim();
  if (!trimmed) return undefined;
  const scalar = singleScalar(value);
  return scalar !== undefined && trimmed === String(scalar) ? undefined : narrative;
}

function singleScalar(value: unknown): string | number | boolean | null | undefined {
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
    return value as string | number | boolean | null;
  }
  if (typeof value !== "object" || Array.isArray(value)) return undefined;
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length !== 1) return undefined;
  const candidate = entries[0]?.[1];
  return candidate === null || ["string", "number", "boolean"].includes(typeof candidate)
    ? (candidate as string | number | boolean | null)
    : undefined;
}

export function data(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function string(value: unknown, fallback = ""): string {
  return value === undefined || value === null ? fallback : String(value);
}

export function number(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function assertNever(value: never): never {
  throw new Error(`Unsupported Fleet UI part: ${JSON.stringify(value)}`);
}
