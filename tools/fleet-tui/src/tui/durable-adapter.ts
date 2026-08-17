/**
 * Durable Session reload (FleetTurn parts) → canonical events (P24/QRE-169).
 * The ONLY place reload wire compat is resolved: camelCase UIMessagePart
 * keys, unwrapped usage, merged dynamic-tool states, and positional stream
 * ids. Lifecycle edges (turn start/finish/status) are live-only by design.
 */

import type { FleetTurn } from "../fleet-api-client.js";
import type { CanonicalEvent } from "./canonical.js";

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function int(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function metadataString(turn: FleetTurn, key: string): string | undefined {
  const value = turn.metadata?.[key];
  return typeof value === "string" ? value : undefined;
}

export function adaptDurableTurns(turns: FleetTurn[]): CanonicalEvent[] {
  const events: CanonicalEvent[] = [];
  for (const turn of turns) {
    const turnId = turn.id;
    events.push({ type: "turn_context", runId: metadataString(turn, "runId") ?? turnId });
    const traceId = metadataString(turn, "traceId");
    if (turn.role === "assistant" && traceId) {
      events.push({
        type: "warning",
        code: "mlflow_trace",
        message: `trace ${traceId}`,
        streamId: `${turnId}:trace`,
        messageId: `${turnId}:trace`,
      });
    }
    const narrative = turn.parts
      .filter(
        (part): part is Extract<(typeof turn.parts)[number], { type: "text" }> =>
          part.type === "text",
      )
      .map((part) => part.text)
      .join("");
    const hasResult = turn.parts.some((part) => part.type === "data-structured-result");
    let currentStep = 0;
    for (const [index, part] of turn.parts.entries()) {
      const messageId = `${turnId}:${index}`;
      const streamId = messageId;
      switch (part.type) {
        case "step-start":
        case "data-step": {
          // Live-only lifecycle: bookkeeping only for durable step attribution.
          const value = part.type === "data-step" ? asRecord(part.data) : {};
          const step = int(value.step);
          if (step !== null) currentStep = step;
          break;
        }
        case "reasoning":
          events.push({
            type: "reasoning",
            streamId,
            step: currentStep || index + 1,
            text: part.text,
            final: true,
            messageId,
          });
          break;
        case "text":
          // With a structured result, narrative folds onto the result message
          // (order-insensitive); without one, text becomes its own message.
          if (!hasResult) {
            events.push({
              type: "text",
              streamId,
              textDelta: part.text,
              final: true,
              role: turn.role === "user" ? "user" : "assistant",
              messageId,
            });
          }
          break;
        case "data-rlm-code": {
          const value = asRecord(part.data);
          events.push({
            type: "code",
            streamId,
            step: (int(value.step) ?? currentStep) || index + 1,
            codeDelta: str(value.code) ?? "",
            isDelta: false,
            final: true,
            messageId,
          });
          break;
        }
        case "data-rlm-output": {
          const value = asRecord(part.data);
          events.push({
            type: "output",
            streamId,
            step: (int(value.step) ?? currentStep) || index + 1,
            outputDelta: str(value.output) ?? "",
            isDelta: false,
            final: true,
            messageId,
          });
          break;
        }
        case "dynamic-tool":
          events.push({
            type: "tool_call",
            toolCallId: part.toolCallId,
            toolName: part.toolName,
            input: part.input,
            messageId,
          });
          if (part.state === "output-available") {
            events.push({
              type: "tool_result",
              toolCallId: part.toolCallId,
              output: part.output,
              messageId,
            });
          } else {
            events.push({
              type: "tool_result",
              toolCallId: part.toolCallId,
              error: part.errorText ?? "Tool failed",
              messageId,
            });
          }
          break;
        case "data-status":
          break;
        case "data-skill": {
          const value = asRecord(part.data);
          events.push({
            type: "skill",
            skillId: str(value.skillId) ?? part.id ?? "(skill)",
            phase: str(value.phase),
            name: str(value.name),
            version: str(value.version),
            trust: str(value.trust),
            affordances: Array.isArray(value.affordances)
              ? value.affordances.filter((item): item is string => typeof item === "string")
              : undefined,
            streamId,
            messageId,
          });
          break;
        }
        case "data-attachment": {
          const value = asRecord(part.data);
          events.push({
            type: "attachment",
            attachmentId: str(value.attachmentId) ?? part.id ?? "(attachment)",
            phase: str(value.phase),
            filename: str(value.filename),
            byteSize: int(value.byteSize),
            streamId,
            messageId,
          });
          break;
        }
        case "data-warning": {
          const value = asRecord(part.data);
          events.push({
            type: "warning",
            code: str(value.code) ?? "warning",
            message: str(value.message) ?? "",
            streamId,
            messageId,
          });
          break;
        }
        case "data-artifact": {
          const value = asRecord(part.data);
          events.push({
            type: "artifact",
            artifactId: str(value.artifactId) ?? part.id ?? "(artifact)",
            artifactKind: str(value.artifactKind) ?? str(value.kind),
            title: str(value.title) ?? str(value.name),
            mediaType: str(value.mediaType),
            byteSize: int(value.byteSize),
            checksumSha256: str(value.checksumSha256),
            streamId,
            messageId,
          });
          break;
        }
        case "data-usage": {
          const value = asRecord(part.data);
          const wrapped = asRecord(value.usage);
          const payload = Object.keys(wrapped).length > 0 ? wrapped : value;
          events.push({
            type: "usage",
            iterations: int(payload.iterations) ?? 0,
            durationMs: int(payload.duration_ms) ?? int(payload.durationMs),
            usage: payload,
            streamId,
            messageId,
          });
          break;
        }
        case "data-structured-result": {
          const value = asRecord(part.data);
          events.push({
            type: "structured_result",
            schemaId: str(value.schemaId) ?? str(value.schema_id) ?? "",
            schemaVersion: str(value.schemaVersion) ?? str(value.schema_version) ?? "",
            value: value.value,
            narrativeText: hasResult && narrative ? narrative : undefined,
            streamId,
            messageId,
          });
          break;
        }
        default:
          throw new Error(`unhandled durable part type: ${(part as { type: string }).type}`);
      }
    }
  }
  return events;
}
