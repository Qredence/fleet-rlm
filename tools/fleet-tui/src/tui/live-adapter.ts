/**
 * Live SSE wire → canonical events (P24/QRE-169). The ONLY place live wire
 * casing/alias and wrapper compat is resolved: snake/camel compat aliases,
 * the data-usage wrapper, and metadata key shapes end here.
 */

import type { FleetUIMessageChunk } from "../sse.js";
import type { CanonicalEvent } from "./canonical.js";
import { asRecord, int, str } from "./coerce.js";

function metadataString(value: unknown, key: string): string | undefined {
  return str(asRecord(value)[key]);
}

export function adaptLiveChunk(chunk: FleetUIMessageChunk): CanonicalEvent[] {
  switch (chunk.type) {
    case "turn_start": {
      const rec = asRecord(chunk);
      return [
        {
          type: "turn_start",
          runId: str(rec.runId) ?? str(rec.run_id) ?? "",
          delivery: rec.delivery === "live" || rec.delivery === "replay" ? rec.delivery : null,
          traceId: str(rec.traceId) ?? str(rec.trace_id) ?? undefined,
        },
      ];
    }
    case "turn_status": {
      const rec = asRecord(chunk);
      return [
        {
          type: "turn_status",
          phase: String(rec.phase ?? "status"),
          detail: str(rec.message) ?? str(rec.status) ?? str(rec.detail),
        },
      ];
    }
    case "step_start":
      return [{ type: "step_start", step: int(asRecord(chunk).step) }];
    case "step_finish":
      return [
        {
          type: "step_finish",
          step: int(asRecord(chunk).step),
          durationMs: int(asRecord(chunk).durationMs) ?? int(asRecord(chunk).duration_ms),
        },
      ];
    case "reasoning": {
      const rec = asRecord(chunk);
      return [
        {
          type: "reasoning",
          streamId: str(rec.streamId) ?? str(rec.stream_id) ?? "1",
          step: int(rec.step) ?? 0,
          text: str(rec.delta) ?? str(rec.text) ?? "",
          final: rec.final === true,
        },
      ];
    }
    case "code": {
      const rec = asRecord(chunk);
      return [
        {
          type: "code",
          streamId: str(rec.streamId) ?? str(rec.stream_id) ?? "1",
          step: int(rec.step) ?? 0,
          codeDelta: str(rec.code) ?? "",
          isDelta: rec.isDelta === true || rec.is_delta === true,
          final: rec.final !== false,
        },
      ];
    }
    case "output": {
      const rec = asRecord(chunk);
      return [
        {
          type: "output",
          streamId: str(rec.streamId) ?? str(rec.stream_id) ?? "1",
          step: int(rec.step) ?? 0,
          outputDelta: str(rec.output) ?? "",
          isDelta: rec.isDelta === true || rec.is_delta === true,
          final: rec.final !== false,
        },
      ];
    }
    case "tool_call": {
      const rec = asRecord(chunk);
      return [
        {
          type: "tool_call",
          toolCallId: str(rec.toolCallId) ?? str(rec.tool_call_id) ?? "",
          toolName: str(rec.toolName) ?? str(rec.tool_name) ?? "",
          input: rec.input,
        },
      ];
    }
    case "tool_result": {
      const rec = asRecord(chunk);
      return [
        {
          type: "tool_result",
          toolCallId: str(rec.toolCallId) ?? str(rec.tool_call_id) ?? "",
          toolName: str(rec.toolName) ?? str(rec.tool_name) ?? undefined,
          output: rec.output,
          error: str(rec.error) ?? undefined,
        },
      ];
    }
    case "text": {
      const rec = asRecord(chunk);
      return [
        {
          type: "text",
          streamId: str(rec.streamId) ?? str(rec.stream_id) ?? "text",
          textDelta: str(rec.delta) ?? str(rec.text) ?? "",
          final: rec.final === true,
          role: "assistant",
        },
      ];
    }
    case "turn_finish": {
      const rec = asRecord(chunk);
      return [
        {
          type: "turn_finish",
          finishReason: str(rec.finishReason) ?? str(rec.finish_reason) ?? "stop",
          durationMs: int(rec.durationMs) ?? int(rec.duration_ms) ?? null,
          checkpointVersion: int(rec.checkpointVersion) ?? int(rec.checkpoint_version) ?? null,
          traceId: str(rec.traceId) ?? str(rec.trace_id) ?? undefined,
        },
      ];
    }
    case "turn_cancelled":
      return [{ type: "turn_cancelled", reason: str(asRecord(chunk).reason) }];
    case "turn_error":
      return [
        {
          type: "error",
          text: str(asRecord(chunk).message) ?? str(asRecord(chunk).text) ?? "Turn failed",
        },
      ];
    case "start": {
      const delivery = metadataString(chunk.messageMetadata, "delivery");
      return [
        {
          type: "turn_start",
          runId: chunk.messageId,
          delivery: delivery === "live" || delivery === "replay" ? delivery : null,
          traceId: metadataString(chunk.messageMetadata, "traceId") ?? undefined,
        },
      ];
    }
    case "start-step":
      return [{ type: "step_start" }];
    case "finish-step":
      return [{ type: "step_finish" }];
    case "reasoning-start":
      return [{ type: "reasoning", streamId: chunk.id, step: 0, text: "", final: false }];
    case "reasoning-delta":
      return [{ type: "reasoning", streamId: chunk.id, step: 0, text: chunk.delta, final: false }];
    case "reasoning-end":
      return [{ type: "reasoning", streamId: chunk.id, step: 0, text: "", final: true }];
    case "text-start":
      return [{ type: "text", streamId: chunk.id, textDelta: "", final: false, role: "assistant" }];
    case "text-delta":
      return [
        {
          type: "text",
          streamId: chunk.id,
          textDelta: chunk.delta,
          final: false,
          role: "assistant",
        },
      ];
    case "text-end":
      return [{ type: "text", streamId: chunk.id, textDelta: "", final: true, role: "assistant" }];
    case "data-rlm-code":
      return [
        {
          type: "code",
          streamId: chunk.data.stream_id || chunk.id || "1",
          step: chunk.data.step ?? 0,
          codeDelta: chunk.data.code,
          isDelta: chunk.data.is_delta === true,
          final: chunk.data.is_final !== false,
        },
      ];
    case "data-rlm-output":
      return [
        {
          type: "output",
          streamId: chunk.data.stream_id || chunk.id || "1",
          step: chunk.data.step ?? 0,
          outputDelta: chunk.data.output,
          isDelta: chunk.data.is_delta === true,
          final: chunk.data.is_final !== false,
        },
      ];
    case "tool-input-available":
      return [
        {
          type: "tool_call",
          toolCallId: chunk.toolCallId,
          toolName: chunk.toolName,
          input: chunk.input,
        },
      ];
    case "tool-output-available":
      return [{ type: "tool_result", toolCallId: chunk.toolCallId, output: chunk.output }];
    case "tool-output-error":
      return [{ type: "tool_result", toolCallId: chunk.toolCallId, error: chunk.errorText }];
    case "data-status": {
      const value = asRecord(chunk.data);
      return [
        {
          type: "turn_status",
          phase: String(value.phase ?? "status"),
          detail: str(value.message) ?? str(value.status) ?? str(value.detail),
        },
      ];
    }
    case "skill":
    case "data-skill": {
      const rec = asRecord(chunk);
      const value = chunk.type === "data-skill" ? asRecord(chunk.data) : rec;
      return [
        {
          type: "skill",
          streamId: str(rec.id) ?? undefined,
          messageId: str(rec.id) ?? undefined,
          skillId: str(value.skill_id) ?? str(value.skillId) ?? str(rec.id) ?? "(skill)",
          phase: str(value.phase),
          name: str(value.name),
          version: str(value.version),
          trust: str(value.trust),
          affordances: Array.isArray(value.affordances)
            ? value.affordances.filter((item): item is string => typeof item === "string")
            : undefined,
        },
      ];
    }
    case "child_progress":
    case "data-child-progress": {
      const rec = asRecord(chunk);
      const value = chunk.type === "data-child-progress" ? asRecord(chunk.data) : rec;
      const states = [
        "not_started",
        "running",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
      ] as const;
      const cleanups = ["pending", "complete", "failed", "not_required"] as const;
      const state = str(value.state);
      const cleanupState = str(value.cleanup_state) ?? str(value.cleanupState);
      const childId = str(value.child_id) ?? str(value.childId);
      const taskLabel = str(value.task_label) ?? str(value.taskLabel);
      if (
        !state ||
        !states.includes(state as (typeof states)[number]) ||
        !cleanupState ||
        !cleanups.includes(cleanupState as (typeof cleanups)[number]) ||
        !childId ||
        !taskLabel
      )
        return [
          {
            type: "turn_status",
            phase: "child",
            detail: "Child progress details are unavailable.",
          },
        ];
      const resultFileCount = int(value.result_file_count) ?? int(value.resultFileCount);
      const elapsedMs = int(value.elapsed_ms) ?? int(value.elapsedMs) ?? 0;
      const rawCode = str(value.code_excerpt) ?? str(value.codeExcerpt);
      const codeExcerpt = rawCode && rawCode.length <= 800 ? rawCode : undefined;
      const rawOutput = str(value.output_excerpt) ?? str(value.outputExcerpt);
      const outputExcerpt = rawOutput && rawOutput.length <= 800 ? rawOutput : undefined;
      return [
        {
          type: "child_progress",
          childId,
          parentRunId: str(value.parent_run_id) ?? str(value.parentRunId) ?? undefined,
          taskLabel,
          state: state as (typeof states)[number],
          elapsedMs,
          outcome: str(value.outcome),
          evidence:
            Array.isArray(value.evidence) &&
            value.evidence.length <= 8 &&
            value.evidence.every((item) => typeof item === "string" && item.length <= 200)
              ? value.evidence
              : undefined,
          gaps:
            Array.isArray(value.gaps) &&
            value.gaps.length <= 8 &&
            value.gaps.every((item) => typeof item === "string" && item.length <= 200)
              ? value.gaps
              : undefined,
          resultFileCount:
            resultFileCount !== undefined && resultFileCount >= 0 && resultFileCount <= 16
              ? resultFileCount
              : undefined,
          codeExcerpt,
          outputExcerpt,
          cleanupState: cleanupState as (typeof cleanups)[number],
          messageId: str(rec.id) ?? undefined,
        },
      ];
    }
    case "attachment":
    case "data-attachment": {
      const rec = asRecord(chunk);
      const value = chunk.type === "data-attachment" ? asRecord(chunk.data) : rec;
      return [
        {
          type: "attachment",
          streamId: str(rec.id) ?? undefined,
          messageId: str(rec.id) ?? undefined,
          attachmentId:
            str(value.attachment_id) ?? str(value.attachmentId) ?? str(rec.id) ?? "(attachment)",
          phase: str(value.phase),
          filename: str(value.filename),
          byteSize: int(value.byte_size) ?? int(value.byteSize) ?? null,
        },
      ];
    }
    case "warning":
    case "data-warning": {
      const rec = asRecord(chunk);
      const value = chunk.type === "data-warning" ? asRecord(chunk.data) : rec;
      return [
        {
          type: "warning",
          streamId: str(rec.id) ?? undefined,
          messageId: str(rec.id) ?? undefined,
          code: str(value.code) ?? "warning",
          message: str(value.message) ?? "",
        },
      ];
    }
    case "artifact":
    case "data-artifact": {
      const rec = asRecord(chunk);
      const value = chunk.type === "data-artifact" ? asRecord(chunk.data) : rec;
      return [
        {
          type: "artifact",
          streamId: str(rec.id) ?? undefined,
          messageId: str(rec.id) ?? undefined,
          artifactId:
            str(value.artifact_id) ?? str(value.artifactId) ?? str(rec.id) ?? "(artifact)",
          artifactKind: str(value.artifact_kind) ?? str(value.artifactKind) ?? str(value.kind),
          title: str(value.title) ?? str(value.name),
          mediaType: str(value.media_type) ?? str(value.mediaType),
          byteSize: int(value.byte_size) ?? int(value.byteSize) ?? null,
          checksumSha256: str(value.checksum_sha256) ?? str(value.checksumSha256),
        },
      ];
    }
    case "usage":
    case "data-usage": {
      const rec = asRecord(chunk);
      const rawUsage = chunk.type === "data-usage" ? asRecord(chunk.data).usage : rec.usage;
      const wrapped = asRecord(rawUsage);
      const iterations = int(rec.iterations) ?? int(wrapped.iterations) ?? 0;
      const durationMs =
        int(rec.durationMs) ??
        int(rec.duration_ms) ??
        int(wrapped.duration_ms) ??
        int(wrapped.durationMs) ??
        null;
      return [
        {
          type: "usage",
          iterations,
          durationMs,
          usage: wrapped,
          streamId: str(rec.id) ?? undefined,
          messageId: str(rec.id) ?? undefined,
        },
      ];
    }
    case "structured_result":
    case "data-structured-result": {
      const rec = asRecord(chunk);
      const value = chunk.type === "data-structured-result" ? asRecord(chunk.data) : rec;
      return [
        {
          type: "structured_result",
          streamId: str(rec.id) ?? undefined,
          messageId: str(rec.id) ?? undefined,
          schemaId: str(value.schema_id) ?? str(value.schemaId) ?? "",
          schemaVersion: str(value.schema_version) ?? str(value.schemaVersion) ?? "",
          value: value.value,
        },
      ];
    }
    case "finish": {
      const metadata = asRecord(chunk.messageMetadata);
      const durationMs = int(metadata.durationMs) ?? int(metadata.duration_ms) ?? null;
      const checkpoint =
        int(metadata.checkpointVersion) ?? int(metadata.checkpoint_version) ?? null;
      return [
        {
          type: "turn_finish",
          finishReason: chunk.finishReason,
          durationMs: durationMs ?? null,
          checkpointVersion: checkpoint ?? null,
          traceId: metadataString(chunk.messageMetadata, "traceId") ?? undefined,
        },
      ];
    }
    case "abort":
      return [{ type: "turn_cancelled", reason: chunk.reason }];
    case "error":
      return [{ type: "error", text: chunk.errorText }];
    default:
      return [];
  }
}
