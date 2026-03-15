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
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (!value) return "";

  if (typeof value === "object" && !Array.isArray(value)) {
    const rec = value as Record<string, unknown>;
    const extracted = rec.text ?? rec.output ?? rec.result ?? rec.message;
    if (typeof extracted === "string") return extracted;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function compact(value: string, _max = 120): string {
  const compacted = value.trim();
  if (!compacted) return "";
  return compacted;
}

function firstLine(value: string, max = 160): string {
  const normalized = compact(value, max);
  if (!normalized) return "";
  const [line] = normalized.split(/\r?\n/, 1);
  return line?.trim() ?? "";
}

function joinFragments(values: unknown[]): string {
  const merged = values.map((value) => asText(value)).join("");
  return compact(merged.replace(/\s+/g, " "));
}

function summarizeStructuredOutput(value: unknown): string | undefined {
  if (Array.isArray(value)) {
    return value.length === 0
      ? "Output list is empty"
      : `Output list with ${value.length} item${value.length === 1 ? "" : "s"}`;
  }

  if (value && typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>).filter(Boolean);
    if (keys.length === 0) return "Structured output";

    const preview = keys.slice(0, 3).join(", ");
    const extra = keys.length - 3;
    return extra > 0 ? `Output fields: ${preview} +${extra} more` : `Output fields: ${preview}`;
  }

  const text = compact(asText(value), 120);
  return text ? `Output: ${text}` : undefined;
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
    const outputText = compact(asText(parsedOutput.data.tool_output ?? parsedOutput.data.result));
    if (outputText) return `${name}: ${outputText}`;
    const argText = compact(asText(parsedOutput.data.tool_input ?? parsedOutput.data.tool_args));
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
  const thought = typeof data.thought === "string" ? compact(data.thought, 100) : undefined;
  const action =
    typeof data.tool_name === "string"
      ? `Action: ${data.tool_name}`
      : typeof data.label === "string"
        ? `Action: ${compact(data.label, 60)}`
        : undefined;
  const observation = compact(asText(data.output), 100);
  return [thought && `Thought: ${thought}`, action, observation && `Obs: ${observation}`]
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
      const combined = joinFragments(output.reasoning);
      return combined ? `Reasoning: ${combined}` : step.label;
    }
    if (Array.isArray(output?.status) && output.status.length > 0) {
      const combined = joinFragments(output.status);
      return combined ? `Status: ${combined}` : step.label;
    }
    // Unpack the {streaming, text} envelope emitted by the backend LLM step
    if (typeof output?.text === "string" && output.text.trim()) {
      return compact(output.text, 120);
    }
  }
  if (step.type === "output") {
    const out = asRecord(step.output);
    const payload = out?.payload;
    const parsedPayload = parseArtifactPayload(payload);
    if (parsedPayload.kind === "error") {
      const record = "error" in parsedPayload.data ? parsedPayload.data.error : parsedPayload.data;
      const msg =
        (typeof record.message === "string" && record.message) ||
        (typeof record.traceback === "string" && record.traceback) ||
        "Execution failed";
      return `Error: ${firstLine(msg, 120)}`;
    }
    const text = typeof out?.text === "string" ? out.text : "";
    if (text.trim()) return compact(text, 120);
    if (payload != null) {
      return summarizeStructuredOutput(payload) || "Structured output";
    }
  }

  // Generic fallback for plain objects
  const genericRecord = asRecord(step.output);
  if (genericRecord) {
    const textCandidate =
      genericRecord.text ?? genericRecord.output ?? genericRecord.result ?? genericRecord.message;
    if (typeof textCandidate === "string" && textCandidate.trim()) {
      return compact(textCandidate, 120);
    }
    return step.label; // Prevents stringifying a raw unparsed object payload
  }

  // If output is a raw string, try parsing it as JSON to prevent JSON representation leaks
  if (typeof step.output === "string") {
    try {
      const parsed = JSON.parse(step.output);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const textCandidate = parsed.text ?? parsed.output ?? parsed.result ?? parsed.message;
        if (typeof textCandidate === "string" && textCandidate.trim()) {
          return compact(textCandidate, 120);
        }
        return step.label; // Was a JSON object, but no readable text discovered, do not leak raw JSON
      }
    } catch {
      // Not JSON, safe to use as a raw human-readable string
    }
    return compact(step.output, 120) || step.label;
  }

  // Final fallback (numbers, booleans, etc.)
  if (step.output != null) {
    return compact(asText(step.output), 120) || step.label;
  }

  const generic = compact(asText(step.input));
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

export function buildArtifactPreviewModel(step: ExecutionStep | undefined): ArtifactPreviewModel {
  if (!step) return { kind: "empty" };

  const outputRecord = asRecord(step.output);
  const text =
    (typeof outputRecord?.text === "string" && outputRecord.text) ||
    (typeof step.output === "string" && step.output) ||
    "";
  const payload =
    outputRecord?.payload ??
    (outputRecord && parseFinalOutputEnvelope(outputRecord) ? outputRecord : undefined);

  if (/error|failed|exception/i.test(step.label)) {
    const parsedErr = parseArtifactPayload(payload ?? step.output ?? step.input);
    if (parsedErr.kind === "error") {
      const record = "error" in parsedErr.data ? parsedErr.data.error : parsedErr.data;
      const message = (typeof record.message === "string" && record.message) || "Execution failed";
      const details =
        (typeof record.traceback === "string" && record.traceback) ||
        (typeof record.stack === "string" && record.stack) ||
        undefined;
      return { kind: "error", message, details };
    }
    if (text.trim()) return { kind: "error", message: firstLine(text), details: text };
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
    return looksLikeMarkdown(text) ? { kind: "markdown", text } : { kind: "text", text };
  }

  if (step.output != null) return { kind: "json", value: step.output };
  if (step.input != null) return { kind: "json", value: step.input };
  return { kind: "text", text: "No preview output was captured for this run." };
}
