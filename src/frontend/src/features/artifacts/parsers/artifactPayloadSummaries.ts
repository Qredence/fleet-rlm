import type { ExecutionStep } from "@/stores/artifactStore";
import {
  parseArtifactPayload,
  parseFinalOutputEnvelope,
} from "@/features/artifacts/parsers/artifactPayloadSchemas";

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  return value as Record<string, unknown>;
}

export function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function compact(value: string, max = 120): string {
  const compacted = value.replace(/\s+/g, " ").trim();
  if (!compacted) return "";
  return compacted.length > max ? `${compacted.slice(0, max)}…` : compacted;
}

function firstLine(value: string, max = 160): string {
  const line = value.split(/\r?\n/, 1)[0] ?? "";
  return compact(line, max);
}

function summarizeToolStep(step: ExecutionStep): string | undefined {
  const inputRecord = asRecord(step.input);
  const outputRecord = asRecord(step.output);
  const toolName =
    (typeof inputRecord?.tool_name === "string" && inputRecord.tool_name) ||
    (typeof outputRecord?.tool_name === "string" && outputRecord.tool_name) ||
    undefined;

  const parsedOutput = parseArtifactPayload(step.output);
  if (parsedOutput.kind === "tool") {
    const name = parsedOutput.data.tool_name || toolName || "tool";
    const outputText = compact(
      asText(parsedOutput.data.tool_output ?? parsedOutput.data.result),
    );
    if (outputText) return `${name}: ${outputText}`;
    const argText = compact(
      asText(parsedOutput.data.tool_input ?? parsedOutput.data.tool_args),
    );
    if (argText) return `${name}(${argText})`;
    return `Tool ${name} executed`;
  }

  const source = compact(asText(step.output ?? step.input));
  if (toolName && source) return `${toolName}: ${source}`;
  if (toolName) return `Tool ${toolName} executed`;
  return source || undefined;
}

function summarizeReplStep(step: ExecutionStep): string | undefined {
  const inputRecord = asRecord(step.input);
  const codeCandidate =
    (typeof inputRecord?.code === "string" && inputRecord.code) ||
    (typeof inputRecord?.source === "string" && inputRecord.source) ||
    (typeof inputRecord?.script === "string" && inputRecord.script) ||
    undefined;
  if (codeCandidate) return `REPL code: ${firstLine(codeCandidate)}`;
  const source = compact(asText(step.output ?? step.input));
  return source ? `REPL: ${source}` : undefined;
}

function summarizeTrajectoryLike(step: ExecutionStep): string | undefined {
  const parsed = parseArtifactPayload(step.output);
  if (parsed.kind !== "trajectory") return;
  const data =
    "trajectory_step" in parsed.data
      ? parsed.data.trajectory_step
      : "step_data" in parsed.data
        ? parsed.data.step_data
        : parsed.data;
  const thought =
    typeof data.thought === "string" ? compact(data.thought, 100) : undefined;
  const action =
    typeof data.tool_name === "string"
      ? `Action: ${data.tool_name}`
      : typeof data.label === "string"
        ? `Action: ${compact(data.label, 60)}`
        : undefined;
  const observation = compact(asText(data.output), 100);
  return [
    thought && `Thought: ${thought}`,
    action,
    observation && `Obs: ${observation}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function summarizeArtifactStep(step: ExecutionStep): string {
  if (step.type === "tool") {
    return summarizeToolStep(step) || step.label;
  }
  if (step.type === "repl") {
    const trajectorySummary = summarizeTrajectoryLike(step);
    return trajectorySummary || summarizeReplStep(step) || step.label;
  }
  if (step.type === "llm") {
    const output = asRecord(step.output);
    if (Array.isArray(output?.reasoning) && output.reasoning.length > 0) {
      const latest = asText(output.reasoning[output.reasoning.length - 1]);
      return `Reasoning: ${compact(latest, 110)}`;
    }
    if (Array.isArray(output?.status) && output.status.length > 0) {
      const latest = asText(output.status[output.status.length - 1]);
      return `Status: ${compact(latest, 110)}`;
    }
  }
  if (step.type === "output") {
    const out = asRecord(step.output);
    const payload = out?.payload;
    const parsedPayload = parseArtifactPayload(payload);
    if (parsedPayload.kind === "error") {
      const record =
        "error" in parsedPayload.data
          ? parsedPayload.data.error
          : parsedPayload.data;
      const msg =
        (typeof record.message === "string" && record.message) ||
        (typeof record.traceback === "string" && record.traceback) ||
        "Execution failed";
      return `Error: ${firstLine(msg, 120)}`;
    }
    const text = typeof out?.text === "string" ? out.text : "";
    if (text.trim()) return compact(text, 120);
    if (payload != null) return `Structured output (${typeof payload})`;
  }

  const generic = compact(asText(step.output ?? step.input));
  return generic || step.label;
}

export type ArtifactPreviewModel =
  | { kind: "empty" }
  | { kind: "markdown"; text: string }
  | { kind: "text"; text: string }
  | { kind: "error"; message: string; details?: string }
  | { kind: "tool_result"; toolName?: string; input?: string; output?: string }
  | {
      kind: "trajectory";
      thought?: string;
      action?: string;
      observation?: string;
    }
  | { kind: "json"; value: unknown };

function looksLikeMarkdown(value: string): boolean {
  return (
    /^#{1,6}\s/m.test(value) ||
    /^[-*+]\s/m.test(value) ||
    /^\d+\.\s/m.test(value) ||
    /```/.test(value) ||
    /\[[^\]]+\]\([^)]+\)/.test(value)
  );
}

export function buildArtifactPreviewModel(
  step: ExecutionStep | undefined,
): ArtifactPreviewModel {
  if (!step) return { kind: "empty" };

  const outputRecord = asRecord(step.output);
  const text =
    (typeof outputRecord?.text === "string" && outputRecord.text) ||
    (typeof step.output === "string" && step.output) ||
    "";
  const payload =
    outputRecord?.payload ??
    (outputRecord && parseFinalOutputEnvelope(outputRecord)
      ? outputRecord
      : undefined);

  if (/error|failed|exception/i.test(step.label)) {
    const parsedErr = parseArtifactPayload(
      payload ?? step.output ?? step.input,
    );
    if (parsedErr.kind === "error") {
      const record =
        "error" in parsedErr.data ? parsedErr.data.error : parsedErr.data;
      const message =
        (typeof record.message === "string" && record.message) ||
        "Execution failed";
      const details =
        (typeof record.traceback === "string" && record.traceback) ||
        (typeof record.stack === "string" && record.stack) ||
        undefined;
      return { kind: "error", message, details };
    }
    if (text.trim())
      return { kind: "error", message: firstLine(text), details: text };
  }

  const parsedPayload = parseArtifactPayload(payload);
  if (parsedPayload.kind === "trajectory") {
    const data =
      "trajectory_step" in parsedPayload.data
        ? parsedPayload.data.trajectory_step
        : "step_data" in parsedPayload.data
          ? parsedPayload.data.step_data
          : parsedPayload.data;
    return {
      kind: "trajectory",
      thought: typeof data.thought === "string" ? data.thought : undefined,
      action:
        typeof data.tool_name === "string"
          ? data.tool_name
          : typeof data.label === "string"
            ? data.label
            : undefined,
      observation: data.output != null ? asText(data.output) : undefined,
    };
  }

  if (parsedPayload.kind === "tool") {
    return {
      kind: "tool_result",
      toolName: parsedPayload.data.tool_name,
      input: parsedPayload.data.tool_input
        ? asText(parsedPayload.data.tool_input)
        : parsedPayload.data.tool_args
          ? asText(parsedPayload.data.tool_args)
          : undefined,
      output:
        parsedPayload.data.tool_output != null
          ? asText(parsedPayload.data.tool_output)
          : parsedPayload.data.result != null
            ? asText(parsedPayload.data.result)
            : undefined,
    };
  }

  if (payload != null && typeof payload === "object") {
    return { kind: "json", value: payload };
  }

  if (text.trim()) {
    return looksLikeMarkdown(text)
      ? { kind: "markdown", text }
      : { kind: "text", text };
  }

  if (step.output != null) return { kind: "json", value: step.output };
  if (step.input != null) return { kind: "json", value: step.input };
  return { kind: "text", text: "No preview output was captured for this run." };
}
