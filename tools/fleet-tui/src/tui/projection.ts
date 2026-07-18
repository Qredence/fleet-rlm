import type { FleetTurn } from "../fleet-api-client.js";
import type { FleetUIMessageChunk } from "../sse.js";
import type { Message, Role, StoreEvent } from "./store.js";

export type Clock = () => number;

export class LiveTurnProjector {
  private runId = "";
  private counter = 0;
  private readonly messages = new Map<string, Message>();
  private readonly reasoningIds = new Map<string, string>();
  private readonly toolIds = new Map<string, string>();
  private readonly liveOccurrences = new Map<string, number>();
  private resultId: string | undefined;
  private textId: string | undefined;
  private streamError: string | null = null;

  constructor(private readonly clock: Clock = Date.now) {}

  push(chunk: FleetUIMessageChunk): StoreEvent[] {
    switch (chunk.type) {
      case "start":
        this.runId = chunk.messageId;
        return [
          {
            type: "run/start",
            runId: chunk.messageId,
            delivery: delivery(chunk.messageMetadata),
          },
        ];
      case "start-step":
        return [{ type: "run/step-start" }];
      case "finish-step":
        return [{ type: "run/step-finish" }];
      case "finish": {
        const metadata = data(chunk.messageMetadata);
        return [
          {
            type: "run/finish",
            finishReason: chunk.finishReason,
            error: chunk.finishReason === "error" ? this.streamError : null,
            durationMs: nullableNumber(metadata.durationMs ?? metadata.duration_ms),
            checkpointVersion: nullableNumber(
              metadata.checkpointVersion ?? metadata.checkpoint_version,
            ),
          },
        ];
      }
      case "abort":
        return [{ type: "run/cancelled", reason: chunk.reason }];
      case "data-structured-result":
        return this.projectResult(chunk);
      case "reasoning-start": {
        const id = `thinking-${chunk.id}`;
        this.reasoningIds.set(chunk.id, id);
        return this.save(thinking(id, this.runId, inferStep(chunk.id), "", this.clock));
      }
      case "reasoning-delta": {
        const id = this.reasoningIds.get(chunk.id) ?? `thinking-${chunk.id}`;
        const prior = this.messages.get(id);
        const text = prior?.kind === "reasoning" ? prior.text + chunk.delta : chunk.delta;
        return this.save(thinking(id, this.runId, inferStep(chunk.id), text, this.clock));
      }
      case "reasoning-end": {
        const id = this.reasoningIds.get(chunk.id) ?? `thinking-${chunk.id}`;
        const prior = this.messages.get(id);
        if (prior?.kind !== "reasoning") return [];
        return this.save(prior);
      }
      case "text-start": {
        if (this.resultId) return [];
        this.textId = chunk.id;
        return [];
      }
      case "text-delta": {
        if (this.resultId) {
          const prior = this.messages.get(this.resultId);
          if (prior?.kind !== "result") return [];
          return this.save({
            ...prior,
            narrative: normalizedNarrative(`${prior.narrative ?? ""}${chunk.delta}`, prior.value),
          });
        }
        const id = chunk.id;
        this.textId = id;
        const prior = this.messages.get(id);
        const value = prior?.kind === "text" ? prior.text + chunk.delta : chunk.delta;
        return this.save(text(id, "assistant", value, true, this.clock));
      }
      case "text-end": {
        if (this.resultId) return [];
        const id = chunk.id;
        const prior = this.messages.get(id);
        if (prior?.kind !== "text") return [];
        return this.save({ ...prior, streaming: false });
      }
      case "tool-input-available": {
        const id = `tool-${chunk.toolCallId}`;
        this.toolIds.set(chunk.toolCallId, id);
        return this.save(
          tool(
            id,
            this.runId,
            chunk.toolCallId,
            chunk.toolName,
            chunk.input,
            undefined,
            undefined,
            this.clock,
          ),
        );
      }
      case "tool-output-available":
        return this.finishTool(chunk.toolCallId, chunk.output, undefined);
      case "tool-output-error":
        return this.finishTool(chunk.toolCallId, undefined, chunk.errorText);
      case "data-rlm-code":
        return this.projectRlm(chunk, "code");
      case "data-rlm-output":
        return this.projectRlm(chunk, "output");
      case "data-status": {
        const value = data(chunk.data);
        return [
          {
            type: "run/status",
            phase: string(value.phase),
            detail: string(value.message ?? value.status ?? value.detail),
          },
        ];
      }
      case "data-skill":
        return this.save(
          skill(
            this.liveId(chunk.type, chunk.id),
            this.runId,
            chunk.id,
            data(chunk.data),
            this.clock,
          ),
        );
      case "data-attachment":
        return this.save(
          attachment(
            this.liveId(chunk.type, chunk.id),
            this.runId,
            chunk.id,
            data(chunk.data),
            this.clock,
          ),
        );
      case "data-warning":
        return this.save(
          warning(this.liveId(chunk.type, chunk.id), this.runId, data(chunk.data), this.clock),
        );
      case "data-artifact":
        return this.save(
          artifact(
            this.liveId(chunk.type, chunk.id),
            this.runId,
            chunk.id,
            data(chunk.data),
            this.clock,
          ),
        );
      case "data-usage":
        return this.save(
          usage(this.liveId(chunk.type, chunk.id), this.runId, data(chunk.data), this.clock),
        );
      case "error":
        this.streamError = chunk.errorText;
        return this.save({
          id: this.liveId("error"),
          kind: "error",
          text: chunk.errorText,
          ts: this.clock(),
        });
      default:
        return assertNever(chunk);
    }
  }

  private projectRlm(
    chunk: Extract<FleetUIMessageChunk, { type: "data-rlm-code" | "data-rlm-output" }>,
    field: "code" | "output",
  ): StoreEvent[] {
    const value = data(chunk.data);
    const step = number(value.step, inferStep(chunk.id));
    const id = `${field}-${chunk.id ?? `${this.runId}-${step}`}`;
    const content = string(field === "code" ? value.code : value.output);
    if (!content) return [];
    return this.save(
      field === "code"
        ? code(id, this.runId, step, content, this.clock)
        : output(id, this.runId, step, content, this.clock),
    );
  }

  private projectResult(
    chunk: Extract<FleetUIMessageChunk, { type: "data-structured-result" }>,
  ): StoreEvent[] {
    const value = data(chunk.data);
    const id = this.textId ?? chunk.id ?? `result-${this.runId}`;
    const prior = this.messages.get(id);
    this.resultId = id;
    return this.save(
      result(
        id,
        this.runId,
        string(value.schemaId ?? value.schema_id),
        string(value.schemaVersion ?? value.schema_version),
        value.value,
        prior?.kind === "text" ? normalizedNarrative(prior.text, value.value) : undefined,
        this.clock,
      ),
    );
  }

  private finishTool(callId: string, output: unknown, error: string | undefined): StoreEvent[] {
    const id = this.toolIds.get(callId) ?? `tool-${callId}`;
    const prior = this.messages.get(id);
    const message = tool(
      id,
      this.runId,
      callId,
      prior?.kind === "tool" ? prior.name : "tool",
      prior?.kind === "tool" ? prior.input : undefined,
      output,
      error,
      this.clock,
      prior?.kind === "tool" ? prior.startedAt : undefined,
    );
    return this.save(message);
  }

  private liveId(type: string, provided?: string): string {
    if (provided) {
      const key = `${type}:${provided}`;
      const occurrence = this.liveOccurrences.get(key) ?? 0;
      this.liveOccurrences.set(key, occurrence + 1);
      return occurrence === 0 ? provided : `${provided}:${occurrence}`;
    }
    this.counter += 1;
    return `${this.runId}:${type}:${this.counter}`;
  }

  private save(message: Message): StoreEvent[] {
    this.messages.set(message.id, message);
    return [{ type: "message/upsert", message }];
  }
}

function delivery(value: unknown): "live" | "replay" | null {
  const metadata = data(value);
  return metadata.delivery === "live" || metadata.delivery === "replay" ? metadata.delivery : null;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function projectDurableTurns(turns: FleetTurn[], clock: Clock = Date.now): StoreEvent[] {
  const messages: Message[] = [];
  for (const turn of turns) {
    const runId = metadataString(turn, "runId") ?? turn.id;
    const resultIndex = turn.parts.findIndex((part) => part.type === "data-structured-result");
    const narrative = turn.parts
      .filter((part) => part.type === "text")
      .map((part) => (part.type === "text" ? (part.text ?? "") : ""))
      .join("");
    let currentStep = 0;
    for (const [index, part] of turn.parts.entries()) {
      const id = `${turn.id}:${index}`;
      const value = data(part.data);
      switch (part.type) {
        case "step-start":
          break;
        case "data-step":
          currentStep = number(value.step, currentStep);
          break;
        case "data-structured-result":
          messages.push(
            result(
              id,
              runId,
              string(value.schemaId ?? value.schema_id),
              string(value.schemaVersion ?? value.schema_version),
              value.value,
              normalizedNarrative(narrative, value.value),
              clock,
            ),
          );
          break;
        case "text":
          if (resultIndex < 0) {
            messages.push(text(id, turn.role as Role, part.text ?? "", false, clock));
          }
          break;
        case "reasoning":
          messages.push(thinking(id, runId, currentStep || index + 1, part.text ?? "", clock));
          break;
        case "dynamic-tool":
          messages.push(
            tool(
              id,
              runId,
              part.toolCallId ?? id,
              part.toolName ?? "tool",
              part.input,
              part.output,
              part.errorText ?? undefined,
              clock,
            ),
          );
          break;
        case "data-rlm-code":
        case "data-rlm-output": {
          const step = number(value.step, currentStep || index + 1);
          const content = string(part.type === "data-rlm-code" ? value.code : value.output);
          if (!content) break;
          currentStep = step;
          messages.push(
            part.type === "data-rlm-code"
              ? code(id, runId, step, content, clock)
              : output(id, runId, step, content, clock),
          );
          break;
        }
        case "data-status":
          break;
        case "data-skill":
          messages.push(skill(id, runId, part.id ?? undefined, value, clock));
          break;
        case "data-attachment":
          messages.push(attachment(id, runId, part.id ?? undefined, value, clock));
          break;
        case "data-warning":
          messages.push(warning(id, runId, value, clock));
          break;
        case "data-artifact":
          messages.push(artifact(id, runId, part.id ?? undefined, value, clock));
          break;
        case "data-usage":
          messages.push(usage(id, runId, value, clock));
          break;
        default:
          assertNever(part.type);
      }
    }
  }
  return messages.map((message) => ({ type: "message/upsert", message }));
}

function text(id: string, role: Role, value: string, streaming: boolean, clock: Clock): Message {
  return { id, kind: "text", role, text: value, streaming, ts: clock() };
}

function thinking(id: string, runId: string, step: number, value: string, clock: Clock): Message {
  return { id, kind: "reasoning", runId, step, text: value, ts: clock() };
}

function tool(
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
    status: error !== undefined ? "error" : output !== undefined ? "success" : "running",
    ts: clock(),
  };
}

function code(id: string, runId: string, step: number, value: string, clock: Clock): Message {
  return { id, kind: "code", runId, step, code: value, ts: clock() };
}

function output(id: string, runId: string, step: number, value: string, clock: Clock): Message {
  return { id, kind: "output", runId, step, output: value, ts: clock() };
}

function result(
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

function normalizedNarrative(narrative: string, value: unknown): string | undefined {
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

function skill(
  id: string,
  runId: string,
  fallbackId: string | undefined,
  value: Record<string, unknown>,
  clock: Clock,
): Message {
  const phase =
    value.phase === "activated" || value.phase === "loaded"
      ? value.phase
      : value.trust !== undefined && value.trust !== null
        ? "activated"
        : "loaded";
  const trust = optionalString(value.trust);
  return {
    id,
    kind: "skill",
    runId,
    skillId: string(value.skillId ?? value.skill_id ?? fallbackId),
    name: string(value.name, "(skill)"),
    phase,
    version: string(value.version, "1.0.0"),
    ...(trust ? { trust } : {}),
    ts: clock(),
  };
}

function attachment(
  id: string,
  runId: string,
  fallbackId: string | undefined,
  value: Record<string, unknown>,
  clock: Clock,
): Message {
  return {
    id,
    kind: "attachment",
    runId,
    attachmentId: string(value.attachmentId ?? value.attachment_id ?? fallbackId),
    filename: string(value.filename, "(file)"),
    bytes: number(value.byteSize ?? value.byte_size),
    ts: clock(),
  };
}

function warning(id: string, runId: string, value: Record<string, unknown>, clock: Clock): Message {
  return {
    id,
    kind: "warning",
    runId,
    code: string(value.code, "warning"),
    message: string(value.message),
    ts: clock(),
  };
}

function artifact(
  id: string,
  runId: string,
  fallbackId: string | undefined,
  value: Record<string, unknown>,
  clock: Clock,
): Message {
  return {
    id,
    kind: "artifact",
    runId,
    artifactId: string(value.artifactId ?? value.artifact_id ?? fallbackId),
    name: string(value.title ?? value.name, "(artifact)"),
    artifactKind: string(value.kind, "file"),
    bytes: number(value.byteSize ?? value.byte_size),
    ts: clock(),
  };
}

function usage(id: string, runId: string, source: Record<string, unknown>, clock: Clock): Message {
  const value = data(source.usage ?? source);
  const observedLmUsage = data(value.observed_lm_usage ?? value.observedLmUsage);
  return {
    id,
    kind: "usage",
    runId,
    iterations: number(value.iterations),
    prompt: observedTokens(observedLmUsage, "prompt_tokens", "promptTokens"),
    completion: observedTokens(observedLmUsage, "completion_tokens", "completionTokens"),
    durationMs: number(value.duration_ms ?? value.durationMs),
    observedLmUsage,
    ts: clock(),
  };
}

function observedTokens(value: Record<string, unknown>, snake: string, camel: string): number {
  const direct = value[snake] ?? value[camel];
  if (direct !== undefined) return number(direct);
  return Object.values(value).reduce<number>(
    (total, nested) => total + observedTokens(data(nested), snake, camel),
    0,
  );
}

function data(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function string(value: unknown, fallback = ""): string {
  return value === undefined || value === null ? fallback : String(value);
}

function optionalString(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  const result = String(value);
  return result || undefined;
}

function number(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function inferStep(id: string | undefined): number {
  const match = id?.match(/(\d+)(?!.*\d)/);
  return match?.[1] ? Number(match[1]) : 0;
}

function metadataString(turn: FleetTurn, key: string): string | undefined {
  const value = turn.metadata?.[key];
  return typeof value === "string" ? value : undefined;
}

function assertNever(value: never): never {
  throw new Error(`Unsupported Fleet UI part: ${JSON.stringify(value)}`);
}
