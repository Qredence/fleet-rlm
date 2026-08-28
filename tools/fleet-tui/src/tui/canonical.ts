/**
 * Canonical semantic Turn event vocabulary (P24/QRE-168; aligned with the
 * backend RuntimeEvent vocabulary). Live SSE and durable reload adapt INTO
 * these types; wire casing/wrapper compat belongs to the adapters only.
 * The shared turn reducer consumes nothing but this union.
 */

export type CanonicalDelivery = "live" | "replay" | null;

export interface TurnStartEvent {
  type: "turn_start";
  runId: string;
  delivery: CanonicalDelivery;
  traceId?: string | undefined;
}

export interface TurnContextEvent {
  /** Adapter-provided run identity for message scoping; NOT a lifecycle event. */
  type: "turn_context";
  runId: string;
}

export interface TurnStatusEvent {
  type: "turn_status";
  phase: string;
  detail?: string | undefined;
}

export interface TurnFinishEvent {
  type: "turn_finish";
  finishReason: string;
  error?: string | null | undefined;
  durationMs?: number | null | undefined;
  checkpointVersion?: number | null | undefined;
  traceId?: string | undefined;
}

export interface TurnCancelledEvent {
  type: "turn_cancelled";
  reason?: string | null | undefined;
}

export interface TurnErrorEvent {
  type: "error";
  text: string;
}

export interface StepStartEvent {
  type: "step_start";
  step?: number | null | undefined;
}

export interface StepFinishEvent {
  type: "step_finish";
  step?: number | null | undefined;
  durationMs?: number | null | undefined;
}

export interface ReasoningEvent {
  /** Adapter-provided message identity seed (positional reload ids, wire ids). */
  type: "reasoning";
  streamId: string;
  step: number;
  text: string;
  final: boolean;
  messageId?: string | undefined;
}

export interface TextEvent {
  /** Adapter-provided message identity seed (positional reload ids, wire ids). */
  type: "text";
  streamId: string;
  textDelta: string;
  final: boolean;
  role?: "user" | "assistant" | undefined;
  messageId?: string | undefined;
}

export interface CodeEvent {
  /** Adapter-provided message identity seed (positional reload ids, wire ids). */
  type: "code";
  streamId: string;
  step: number;
  codeDelta: string;
  isDelta: boolean;
  final: boolean;
  messageId?: string | undefined;
}

export interface OutputEvent {
  /** Adapter-provided message identity seed (positional reload ids, wire ids). */
  type: "output";
  streamId: string;
  step: number;
  outputDelta: string;
  isDelta: boolean;
  final: boolean;
  messageId?: string | undefined;
}

export interface ToolCallEvent {
  /** Adapter-provided message identity seed (positional reload ids, wire ids). */
  type: "tool_call";
  toolCallId: string;
  toolName: string;
  input: unknown;
  messageId?: string | undefined;
}

export interface ToolResultEvent {
  /** Adapter-provided message identity seed (positional reload ids, wire ids). */
  type: "tool_result";
  toolCallId: string;
  toolName?: string | undefined;
  output?: unknown;
  error?: string | undefined;
  messageId?: string | undefined;
}

export interface SkillEvent {
  /** Adapter-provided positional/wire identity (message minting seed). */
  streamId?: string | undefined;
  messageId?: string | undefined;
  type: "skill";
  skillId: string;
  phase?: string | undefined;
  name?: string | undefined;
  version?: string | undefined;
  trust?: string | undefined;
  affordances?: string[] | undefined;
}

export interface AttachmentEvent {
  /** Adapter-provided positional/wire identity (message minting seed). */
  streamId?: string | undefined;
  messageId?: string | undefined;
  type: "attachment";
  attachmentId: string;
  phase?: string | undefined;
  filename?: string | undefined;
  byteSize?: number | null | undefined;
}

export interface WarningEvent {
  /** Adapter-provided positional/wire identity (message minting seed). */
  streamId?: string | undefined;
  messageId?: string | undefined;
  type: "warning";
  code: string;
  message: string;
}

export interface ArtifactEvent {
  /** Adapter-provided positional/wire identity (message minting seed). */
  streamId?: string | undefined;
  messageId?: string | undefined;
  type: "artifact";
  artifactId: string;
  artifactKind?: string | undefined;
  title?: string | undefined;
  mediaType?: string | undefined;
  byteSize?: number | null | undefined;
  checksumSha256?: string | undefined;
}

export interface UsageEvent {
  /** Adapter-provided positional/wire identity (message minting seed). */
  streamId?: string | undefined;
  messageId?: string | undefined;
  type: "usage";
  iterations: number;
  durationMs?: number | null | undefined;
  usage: Record<string, unknown>;
}

export interface StructuredResultEvent {
  /** Adapter-provided positional/wire identity (message minting seed). */
  streamId?: string | undefined;
  messageId?: string | undefined;
  type: "structured_result";
  schemaId: string;
  schemaVersion: string;
  value: unknown;
  narrativeText?: string | undefined;
}

export type CanonicalEvent =
  | TurnStartEvent
  | TurnContextEvent
  | TurnStatusEvent
  | TurnFinishEvent
  | TurnCancelledEvent
  | TurnErrorEvent
  | StepStartEvent
  | StepFinishEvent
  | ReasoningEvent
  | TextEvent
  | CodeEvent
  | OutputEvent
  | ToolCallEvent
  | ToolResultEvent
  | SkillEvent
  | AttachmentEvent
  | WarningEvent
  | ArtifactEvent
  | UsageEvent
  | StructuredResultEvent;

/**
 * Serialize the client-side canonical event to stable snake_case JSON for
 * cross-language fixtures — undefined/null
 * fields are omitted.
 */
export function serializeCanonicalEvent(event: CanonicalEvent): Record<string, unknown> {
  const keyMap: Record<string, string> = {
    runId: "run_id",
    traceId: "trace_id",
    finishReason: "finish_reason",
    durationMs: "duration_ms",
    checkpointVersion: "checkpoint_version",
    streamId: "stream_id",
    textDelta: "text_delta",
    codeDelta: "code_delta",
    outputDelta: "output_delta",
    isDelta: "is_delta",
    toolCallId: "tool_call_id",
    toolName: "tool_name",
    skillId: "skill_id",
    attachmentId: "attachment_id",
    byteSize: "byte_size",
    artifactId: "artifact_id",
    artifactKind: "artifact_kind",
    mediaType: "media_type",
    checksumSha256: "checksum_sha256",
    schemaId: "schema_id",
    schemaVersion: "schema_version",
    narrativeText: "narrative_text",
    messageId: "message_id",
  };
  const record = event as unknown as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (value === undefined || value === null) continue;
    out[keyMap[key] ?? key] = value;
  }
  return out;
}
