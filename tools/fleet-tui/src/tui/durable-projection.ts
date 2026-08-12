import type { FleetTurn } from "../fleet-api-client.js";
import { summarizeExecution } from "./execution-summary.js";
import {
  artifact,
  assertNever,
  attachment,
  type Clock,
  code,
  data,
  normalizedNarrative,
  number,
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
import type { Message, Role, StoreEvent } from "./store.js";

export function projectDurableTurns(turns: FleetTurn[], clock: Clock = Date.now): StoreEvent[] {
  const messages: Message[] = [];
  for (const turn of turns) {
    const runId = metadataString(turn, "runId") ?? turn.id;
    const durableTraceId = metadataString(turn, "traceId");
    if (turn.role === "assistant" && durableTraceId) {
      messages.push(
        warning(
          `${turn.id}:trace`,
          runId,
          { code: "mlflow_trace", message: `trace ${durableTraceId}` },
          clock,
        ),
      );
    }
    const resultIndex = turn.parts.findIndex((part) => part.type === "data-structured-result");
    const narrative = turn.parts
      .filter(
        (part): part is Extract<(typeof turn.parts)[number], { type: "text" }> =>
          part.type === "text",
      )
      .map((part) => part.text)
      .join("");
    let currentStep = 0;
    for (const [index, part] of turn.parts.entries()) {
      const id = `${turn.id}:${index}`;
      switch (part.type) {
        case "step-start":
          break;
        case "data-step": {
          const value = data(part.data);
          currentStep = number(value.step, currentStep);
          break;
        }
        case "data-structured-result": {
          const value = data(part.data);
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
        }
        case "text":
          if (resultIndex < 0) {
            messages.push(text(id, turn.role as Role, part.text, false, clock));
          }
          break;
        case "reasoning":
          messages.push(thinking(id, runId, currentStep || index + 1, part.text, clock));
          break;
        case "dynamic-tool":
          messages.push(
            tool(
              id,
              runId,
              part.toolCallId,
              part.toolName,
              part.input,
              part.output,
              part.errorText ?? undefined,
              clock,
            ),
          );
          break;
        case "data-rlm-code":
        case "data-rlm-output": {
          const value = data(part.data);
          const step = number(value.step, currentStep || index + 1);
          const content = string(part.type === "data-rlm-code" ? value.code : value.output);
          if (!content) break;
          currentStep = step;
          messages.push(
            part.type === "data-rlm-code"
              ? code(id, runId, step, content, false, clock)
              : output(id, runId, step, content, false, clock),
          );
          break;
        }
        case "data-status":
          break;
        case "data-skill": {
          const value = data(part.data);
          messages.push(skill(id, runId, part.id ?? undefined, value, clock));
          break;
        }
        case "data-attachment": {
          const value = data(part.data);
          messages.push(attachment(id, runId, part.id ?? undefined, value, clock));
          break;
        }
        case "data-warning": {
          const value = data(part.data);
          messages.push(warning(id, runId, value, clock));
          break;
        }
        case "data-artifact": {
          const value = data(part.data);
          messages.push(artifact(id, runId, part.id ?? undefined, value, clock));
          break;
        }
        case "data-usage": {
          const value = data(part.data);
          messages.push(usage(id, runId, value, clock));
          break;
        }
        default:
          assertNever(part);
      }
    }
  }
  return messages.map((message) => ({
    type: "message/upsert",
    message:
      message.kind === "usage"
        ? {
            ...message,
            executionSummary: summarizeExecution(messages, message.runId),
          }
        : message,
  }));
}

function metadataString(turn: FleetTurn, key: string): string | undefined {
  const value = turn.metadata?.[key];
  return typeof value === "string" ? value : undefined;
}
