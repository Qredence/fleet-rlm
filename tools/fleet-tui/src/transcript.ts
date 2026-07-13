import type { FleetTurn, FleetTurnPart } from "./fleet-api-client.js";

const sensitiveKey =
  /(?:api[-_]?key|authorization|credential|password|secret|private[-_]?key|env(?:ironment)?)/i;
const standaloneTokenKey = /(?:^|[-_])token(?:$|[-_])/i;

/** Render the server's durable, already-public turn detail into a readable pre-TUI transcript. */
export function formatTranscript(turns: FleetTurn[]): string {
  if (turns.length === 0) {
    return "";
  }

  const lines = ["\n=== Restored Fleet transcript ==="];
  for (const [index, turn] of turns.entries()) {
    const sequence =
      typeof turn.metadata?.sequence === "number" ? turn.metadata.sequence : index + 1;
    lines.push(`\n[${sequence}] ${label(turn.role)}`);
    for (const part of turn.parts) {
      lines.push(...formatPart(part));
    }
  }
  lines.push("\n=== End restored transcript ===\n");
  return lines.join("\n");
}

function formatPart(part: FleetTurnPart): string[] {
  switch (part.type) {
    case "text":
      return part.text ? [part.text] : [];
    case "reasoning":
      return part.text ? ["Reasoning:", part.text] : [];
    case "dynamic-tool":
      return formatTool(part);
    case "step-start":
      return ["Step started."];
    default:
      if (part.type.startsWith("data-")) {
        return [`${dataLabel(part.type)}:`, formatValue(part.data)];
      }
      return [];
  }
}

function formatTool(part: FleetTurnPart): string[] {
  const lines = [`Tool: ${part.toolName ?? "tool"}`];
  if (part.input !== undefined) {
    lines.push(`input: ${formatValue(part.input)}`);
  }
  if (part.output !== undefined) {
    lines.push(`output: ${formatValue(part.output)}`);
  }
  if (part.errorText) {
    lines.push(`error: ${part.errorText}`);
  }
  return lines;
}

function label(role: string): string {
  return role === "assistant" ? "Assistant" : role === "user" ? "User" : role;
}

function dataLabel(type: string): string {
  return type.slice("data-".length).replaceAll("-", " ");
}

function formatValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(redact(value));
  } catch {
    return String(value);
  }
}

function redact(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(redact);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, nested]) => [
        key,
        sensitiveKey.test(key) || standaloneTokenKey.test(key) ? "[redacted]" : redact(nested),
      ]),
    );
  }
  return value;
}
