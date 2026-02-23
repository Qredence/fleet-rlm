import type { ExecutionStep } from "@/stores/artifactStore";

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function parseToolNameFromLabel(label: string): string | undefined {
  const match = /^tool:\s*(.+)$/i.exec(label.trim());
  const candidate = match?.[1]?.trim();
  return candidate ? candidate : undefined;
}

function toolNameFromPayloadValue(value: unknown): string | undefined {
  const record = asRecord(value);
  if (!record) return undefined;

  const direct = asText(record.tool_name).trim();
  if (direct) return direct;

  const nestedTool = asRecord(record.tool);
  const nestedToolName = asText(nestedTool?.name).trim();
  if (nestedToolName) return nestedToolName;

  const nestedStep = asRecord(record.step);
  const nestedStepName = asText(nestedStep?.tool_name).trim();
  if (nestedStepName) return nestedStepName;

  const nestedPayload = asRecord(record.payload);
  const nestedPayloadName = asText(nestedPayload?.tool_name).trim();
  if (nestedPayloadName) return nestedPayloadName;

  return undefined;
}

export function extractToolBadgeFromStep(step: ExecutionStep): {
  toolName?: string;
  toolNameSource?: "payload" | "label";
} {
  if (step.type !== "tool" && step.type !== "repl") return {};

  const fromPayload =
    toolNameFromPayloadValue(step.input) ??
    toolNameFromPayloadValue(step.output);
  if (fromPayload) {
    return { toolName: fromPayload, toolNameSource: "payload" };
  }

  const fromLabel = parseToolNameFromLabel(step.label);
  if (fromLabel) {
    return { toolName: fromLabel, toolNameSource: "label" };
  }

  return {};
}
