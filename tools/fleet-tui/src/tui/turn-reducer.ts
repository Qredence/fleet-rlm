/**
 * One source-agnostic reducer: canonical events → StoreEvents (P24/QRE-169).
 * Both live streaming and durable reload converge here BEFORE any store
 * mutation; this module does not know or care which wire format produced an
 * event. Fold state (delta accumulation, text→result replacement, tool call
 * pairing, occurrence dedupe, execution summary) lives here exactly once.
 */

import type { CanonicalEvent, CodeEvent, OutputEvent } from "./canonical.js";
import { summarizeExecution } from "./execution-summary.js";
import {
  artifact,
  attachment,
  code,
  normalizedNarrative,
  output,
  result,
  skill,
  text,
  thinking,
  tool,
  usage,
  warning,
  type Clock,
} from "./projection-helpers.js";
import type { Message, StoreEvent } from "./store.js";

function inferStep(streamId: string): number {
  const match = streamId.match(/(\d+)(?!.*\d)/);
  return match?.[1] ? Number(match[1]) : 0;
}

function canonicalReasoningBaseId(streamId: string): string {
  const suffix = ":canonical";
  return streamId.endsWith(suffix) ? streamId.slice(0, -suffix.length) || streamId : streamId;
}

export class TurnEventReducer {
  private runId = "";
  private counter = 0;
  private readonly messages = new Map<string, Message>();
  private readonly reasoningIds = new Map<string, string>();
  private readonly toolIds = new Map<string, string>();
  private readonly occurrences = new Map<string, number>();
  private resultId: string | undefined;
  private textId: string | undefined;
  private streamError: string | null = null;

  constructor(private readonly clock: Clock = Date.now) {}

  push(event: CanonicalEvent): StoreEvent[] {
    switch (event.type) {
      case "turn_start": {
        this.runId = event.runId;
        return [
          {
            type: "run/start",
            runId: event.runId,
            delivery: event.delivery,
            traceId: event.traceId ?? null,
          },
        ];
      }
      case "turn_context":
        // Reload-only scoping: no lifecycle StoreEvent (hydration owns it).
        this.runId = event.runId;
        return [];
      case "turn_status":
        return [{ type: "run/status", phase: event.phase, detail: event.detail ?? "" }];
      case "step_start":
        return [{ type: "run/step-start" }];
      case "step_finish":
        return [{ type: "run/step-finish" }];
      case "turn_finish":
        return [
          {
            type: "run/finish",
            finishReason: event.finishReason,
            error: event.finishReason === "error" ? this.streamError : null,
            durationMs: event.durationMs ?? null,
            checkpointVersion: event.checkpointVersion ?? null,
            traceId: event.traceId ?? null,
          },
        ];
      case "turn_cancelled":
        return [{ type: "run/cancelled", reason: event.reason ?? "cancelled" }];
      case "error": {
        this.streamError = event.text;
        return this.save({
          id: this.positionedId("error"),
          kind: "error",
          text: event.text,
          ts: this.clock(),
        });
      }
      case "reasoning": {
        const baseId = canonicalReasoningBaseId(event.streamId);
        const id = this.reasoningIds.get(baseId) ?? event.messageId ?? `thinking-${baseId}`;
        this.reasoningIds.set(baseId, id);
        const prior = this.messages.get(id);
        const step = event.step > 0 ? event.step : inferStep(event.streamId);
        // Start marker: fresh accumulation resets any stale (:canonical twins
        // reopen the same card with corrected text).
        if (!event.final && event.text.length === 0) {
          return this.save(thinking(id, this.runId, step, "", this.clock));
        }
        if (event.final) {
          if (!event.text) {
            // End marker: close the open card; mint nothing when absent.
            return prior?.kind === "reasoning" ? this.save(prior) : [];
          }
          // Durable single-shot (full text, terminally folded).
          return this.save(thinking(id, this.runId, step, event.text, this.clock));
        }
        const nextText = (prior?.kind === "reasoning" ? prior.text : "") + event.text;
        return this.save(thinking(id, this.runId, step, nextText, this.clock));
      }
      case "text": {
        if (this.resultId) {
          if (event.textDelta.length === 0) return [];
          const prior = this.messages.get(this.resultId);
          if (prior?.kind !== "result") return [];
          return this.save({
            ...prior,
            narrative: normalizedNarrative(
              `${prior.narrative ?? ""}${event.textDelta}`,
              prior.value,
            ),
          });
        }
        const id = event.messageId ?? event.streamId;
        const prior = this.messages.get(id);
        const value = (prior?.kind === "text" ? prior.text : "") + event.textDelta;
        if (event.textDelta.length === 0 && !prior) return [];
        this.textId = id;
        return this.save(
          text(id, event.role ?? "assistant", value, !event.final, this.clock, this.runId),
        );
      }
      case "code":
        return this.projectStreamedPart(event, "code");
      case "output":
        return this.projectStreamedPart(event, "output");
      case "tool_call": {
        const id = event.messageId ?? `tool-${event.toolCallId}`;
        this.toolIds.set(event.toolCallId, id);
        return this.save(
          tool(
            id,
            this.runId,
            event.toolCallId,
            event.toolName,
            event.input,
            undefined,
            undefined,
            this.clock,
          ),
        );
      }
      case "tool_result": {
        const id =
          this.toolIds.get(event.toolCallId) ?? event.messageId ?? `tool-${event.toolCallId}`;
        const prior = this.messages.get(id);
        const message = tool(
          id,
          this.runId,
          event.toolCallId,
          event.toolName ?? (prior?.kind === "tool" ? prior.name : "tool"),
          prior?.kind === "tool" ? prior.input : undefined,
          event.output,
          event.error,
          this.clock,
          prior?.kind === "tool" ? prior.startedAt : undefined,
        );
        return this.save(message);
      }
      case "skill":
        return this.save(
          skill(
            this.positionedId("skill", event.messageId ?? event.skillId),
            this.runId,
            event,
            this.clock,
          ),
        );
      case "attachment":
        return this.save(
          attachment(
            this.positionedId("attachment", event.messageId ?? event.attachmentId),
            this.runId,
            event,
            this.clock,
          ),
        );
      case "warning":
        return this.save(
          warning(
            this.positionedId("warning", event.messageId ?? event.streamId),
            this.runId,
            event,
            this.clock,
          ),
        );
      case "artifact":
        return this.save(
          artifact(
            this.positionedId("artifact", event.messageId ?? event.artifactId),
            this.runId,
            event,
            this.clock,
          ),
        );
      case "usage": {
        const id = this.positionedId("usage", event.messageId ?? event.streamId);
        const message = usage(id, this.runId, event, this.clock);
        if (message.kind === "usage") {
          message.executionSummary = summarizeExecution(
            [...this.messages.values(), message],
            this.runId,
          );
        }
        return this.save(message);
      }
      case "structured_result": {
        const id =
          this.textId ??
          event.messageId ??
          event.streamId ??
          this.positionedId("structured_result");
        const prior = this.messages.get(id);
        this.resultId = id;
        let narrative: string | undefined;
        if (event.narrativeText !== undefined) {
          // Reload: narrative folded by the adapter across part orderings.
          narrative = normalizedNarrative(event.narrativeText, event.value);
        } else if (prior !== undefined) {
          let base = "";
          if (prior.kind === "text") base = prior.text;
          else if (prior.kind === "result") base = prior.narrative ?? "";
          narrative = base ? normalizedNarrative(base, event.value) : undefined;
        }
        return this.save(
          result(
            id,
            this.runId,
            event.schemaId,
            event.schemaVersion,
            event.value,
            narrative,
            this.clock,
          ),
        );
      }
    }
  }

  private projectStreamedPart(
    event: CodeEvent | OutputEvent,
    field: "code" | "output",
  ): StoreEvent[] {
    const id = event.messageId ?? `${field}-${event.streamId}`;
    const prior = this.messages.get(id);
    const content =
      field === "code" ? (event as CodeEvent).codeDelta : (event as OutputEvent).outputDelta;
    if (!content) {
      if (!event.final) return [];
      if (field === "code" && prior?.kind === "code" && prior.streaming) {
        return this.save({ ...prior, streaming: false });
      }
      if (field === "output" && prior?.kind === "output" && prior.streaming) {
        return this.save({ ...prior, streaming: false });
      }
      return [];
    }
    let priorContent = "";
    if (field === "code" && prior?.kind === "code") {
      priorContent = prior.code;
    } else if (field === "output" && prior?.kind === "output") {
      priorContent = prior.output;
    }
    const nextContent = event.isDelta ? `${priorContent}${content}` : content;
    return this.save(
      field === "code"
        ? code(id, this.runId, event.step, nextContent, !event.final, this.clock)
        : output(id, this.runId, event.step, nextContent, !event.final, this.clock),
    );
  }

  private positionedId(kind: string, provided?: string): string {
    if (provided) {
      const key = `${kind}:${provided}`;
      const occurrence = this.occurrences.get(key) ?? 0;
      this.occurrences.set(key, occurrence + 1);
      return occurrence === 0 ? provided : `${provided}:${occurrence}`;
    }
    this.counter += 1;
    return `${this.runId}:${kind}:${this.counter}`;
  }

  private save(message: Message): StoreEvent[] {
    this.messages.set(message.id, message);
    return [{ type: "message/upsert", message }];
  }
}
