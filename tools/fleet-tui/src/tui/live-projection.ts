import type { FleetUIMessageChunk } from "../sse.js";
import {
  artifact,
  assertNever,
  attachment,
  type Clock,
  code,
  data,
  normalizedNarrative,
  nullableNumber,
  number,
  optionalString,
  output,
  result,
  skill,
  string,
  text,
  thinking,
  tool,
  usage,
  warning,
} from "./projection-helpers.js";
import type { Message, StoreEvent } from "./store.js";

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
            traceId: metadataStringValue(chunk.messageMetadata, "traceId"),
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
            traceId: metadataStringValue(chunk.messageMetadata, "traceId"),
          },
        ];
      }
      case "abort":
        return [{ type: "run/cancelled", reason: chunk.reason }];
      case "data-structured-result":
        return this.projectResult(chunk);
      case "reasoning-start": {
        const canonicalBaseId = canonicalReasoningBaseId(chunk.id);
        const id = canonicalBaseId
          ? (this.reasoningIds.get(canonicalBaseId) ?? `thinking-${canonicalBaseId}`)
          : `thinking-${chunk.id}`;
        this.reasoningIds.set(chunk.id, id);
        return this.save(thinking(id, this.runId, inferStep(chunk.id), "", this.clock));
      }
      case "reasoning-delta": {
        const id = this.reasoningIds.get(chunk.id) ?? `thinking-${chunk.id}`;
        const prior = this.messages.get(id);
        const nextText = prior?.kind === "reasoning" ? prior.text + chunk.delta : chunk.delta;
        return this.save(thinking(id, this.runId, inferStep(chunk.id), nextText, this.clock));
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
        if (chunk.delta.length === 0) return [];
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
        return this.save(text(id, "assistant", value, true, this.clock, this.runId));
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
    const streamId =
      optionalString(value.stream_id) ?? optionalString(chunk.id) ?? `${this.runId}-${step}`;
    const id = `${field}-${streamId}`;
    const prior = this.messages.get(id);
    const isDelta = value.is_delta === true;
    const isFinal = value.is_final === true || !isDelta;
    const content = string(field === "code" ? value.code : value.output);
    if (!content) {
      if (!isFinal) return [];
      if (field === "code" && prior?.kind === "code" && prior.streaming) {
        return this.save({ ...prior, streaming: false });
      }
      if (field === "output" && prior?.kind === "output" && prior.streaming) {
        return this.save({ ...prior, streaming: false });
      }
      return [];
    }
    let priorContent = "";
    if (field === "code") {
      if (prior?.kind === "code") {
        priorContent = prior.code;
      }
    } else if (prior?.kind === "output") {
      priorContent = prior.output;
    }
    const nextContent = isDelta ? `${priorContent}${content}` : content;
    return this.save(
      field === "code"
        ? code(id, this.runId, step, nextContent, !isFinal, this.clock)
        : output(id, this.runId, step, nextContent, !isFinal, this.clock),
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

  private finishTool(callId: string, toolOutput: unknown, error: string | undefined): StoreEvent[] {
    const id = this.toolIds.get(callId) ?? `tool-${callId}`;
    const prior = this.messages.get(id);
    const message = tool(
      id,
      this.runId,
      callId,
      prior?.kind === "tool" ? prior.name : "tool",
      prior?.kind === "tool" ? prior.input : undefined,
      toolOutput,
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

function inferStep(id: string | undefined): number {
  const match = id?.match(/(\d+)(?!.*\d)/);
  return match?.[1] ? Number(match[1]) : 0;
}

function canonicalReasoningBaseId(id: string): string | undefined {
  const suffix = ":canonical";
  if (!id.endsWith(suffix)) return undefined;
  const base = id.slice(0, -suffix.length);
  return base || undefined;
}

function metadataStringValue(value: unknown, key: string): string | null {
  const metadata = data(value);
  const item = metadata[key];
  return typeof item === "string" && item.length > 0 ? item : null;
}
