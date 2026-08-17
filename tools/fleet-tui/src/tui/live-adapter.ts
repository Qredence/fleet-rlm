/**
 * Live SSE wire → canonical events (P24/QRE-169). The ONLY place live wire
 * casing/alias and wrapper compat is resolved: snake/camel compat aliases,
 * the data-usage wrapper, and metadata key shapes end here.
 */

import type { FleetUIMessageChunk } from "../sse.js";
import type { CanonicalEvent } from "./canonical.js";

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function int(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) ? value : undefined;
}

function metadataString(value: unknown, key: string): string | undefined {
  return str(asRecord(value)[key]);
}

export function adaptLiveChunk(chunk: FleetUIMessageChunk): CanonicalEvent[] {
  switch (chunk.type) {
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
    case "data-skill": {
      const value = asRecord(chunk.data);
      return [
        {
          type: "skill",
          streamId: chunk.id ?? undefined,
          messageId: chunk.id ?? undefined,
          skillId: str(value.skill_id) ?? str(value.skillId) ?? chunk.id ?? "(skill)",
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
    case "data-attachment": {
      const value = asRecord(chunk.data);
      return [
        {
          type: "attachment",
          streamId: chunk.id ?? undefined,
          messageId: chunk.id ?? undefined,
          attachmentId:
            str(value.attachment_id) ?? str(value.attachmentId) ?? chunk.id ?? "(attachment)",
          phase: str(value.phase),
          filename: str(value.filename),
          byteSize: int(value.byte_size) ?? int(value.byteSize) ?? null,
        },
      ];
    }
    case "data-warning": {
      const value = asRecord(chunk.data);
      return [
        {
          type: "warning",
          streamId: chunk.id ?? undefined,
          messageId: chunk.id ?? undefined,
          code: str(value.code) ?? "warning",
          message: str(value.message) ?? "",
        },
      ];
    }
    case "data-artifact": {
      const value = asRecord(chunk.data);
      return [
        {
          type: "artifact",
          streamId: chunk.id ?? undefined,
          messageId: chunk.id ?? undefined,
          artifactId: str(value.artifact_id) ?? str(value.artifactId) ?? chunk.id ?? "(artifact)",
          artifactKind: str(value.artifact_kind) ?? str(value.kind),
          title: str(value.title) ?? str(value.name),
          mediaType: str(value.media_type) ?? str(value.mediaType),
          byteSize: int(value.byte_size) ?? int(value.byteSize) ?? null,
          checksumSha256: str(value.checksum_sha256) ?? str(value.checksumSha256),
        },
      ];
    }
    case "data-usage": {
      const wrapped = asRecord(asRecord(chunk.data).usage);
      const iterations = int(wrapped.iterations) ?? 0;
      const durationMs = int(wrapped.duration_ms) ?? int(wrapped.durationMs) ?? null;
      return [
        {
          type: "usage",
          iterations,
          durationMs,
          usage: wrapped,
          streamId: chunk.id ?? undefined,
          messageId: chunk.id ?? undefined,
        },
      ];
    }
    case "data-structured-result": {
      const value = asRecord(chunk.data);
      return [
        {
          type: "structured_result",
          streamId: chunk.id ?? undefined,
          messageId: chunk.id ?? undefined,
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
  }
}
