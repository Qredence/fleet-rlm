import type { WsServerMessage } from "../../lib/rlm-api";
import {
  useArtifactStore,
  type ArtifactStepType,
  type ExecutionStep,
} from "../../stores/artifactStore";
import { createLocalId } from "../../lib/id";

function nextId(prefix: string): string {
  return createLocalId(prefix);
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return undefined;
  return value as Record<string, unknown>;
}

function asText(value: unknown): string {
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

function toEpochMs(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1e12 ? value : value * 1000;
  }

  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) return parsed;
  }

  return Date.now();
}

function normalizeStepType(value: unknown): ArtifactStepType {
  const raw = asText(value).trim().toLowerCase();
  if (raw === "repl") return "repl";
  if (raw === "tool") return "tool";
  if (raw === "memory") return "memory";
  if (raw === "output") return "output";
  return "llm";
}

function findStepById(
  steps: ExecutionStep[],
  id?: string,
): ExecutionStep | undefined {
  if (!id) return undefined;
  return steps.find((step) => step.id === id);
}

function getCurrentLlmStep(
  steps: ExecutionStep[],
  activeStepId?: string,
): ExecutionStep | undefined {
  const active = findStepById(steps, activeStepId);
  if (active?.type === "llm") return active;

  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i];
    if (!step) continue;
    if (step.type !== "llm") continue;

    const output = asRecord(step.output);
    if (output?.streaming === false) continue;
    return step;
  }

  return undefined;
}

function upsert(step: ExecutionStep): void {
  useArtifactStore.getState().upsertStep(step);
}

function add(step: ExecutionStep): void {
  useArtifactStore.getState().addStep(step);
}

function setActive(id?: string): void {
  useArtifactStore.getState().setActiveStepId(id);
}

function normalizeExecutionStepFromPayload(
  payload: Record<string, unknown> | undefined,
  fallbackTimestamp: string | undefined,
): ExecutionStep | null {
  const step = asRecord(payload?.step);
  const sourceType = asText(payload?.source_type);

  if (sourceType !== "execution_step" || !step) {
    return null;
  }

  const id = asText(step.id) || nextId("exec");
  const type = normalizeStepType(step.type);
  const label = asText(step.label) || `${type.toUpperCase()} step`;
  const parentIdRaw = asText(step.parent_id).trim();

  return {
    id,
    type,
    label,
    parent_id: parentIdRaw || undefined,
    input: step.input,
    output: step.output,
    timestamp: toEpochMs(step.timestamp ?? fallbackTimestamp),
  };
}

function appendIntoLlmStep(entry: {
  bucket: "tokens" | "reasoning" | "status";
  text: string;
  timestamp: number;
}): void {
  if (!entry.text.trim()) return;

  const { steps, activeStepId } = useArtifactStore.getState();
  const current = getCurrentLlmStep(steps, activeStepId);

  if (!current) {
    const id = nextId("llm");
    add({
      id,
      type: "llm",
      label: "LLM reasoning",
      timestamp: entry.timestamp,
      output: {
        streaming: true,
        text: entry.bucket === "tokens" ? entry.text : "",
        reasoning: entry.bucket === "reasoning" ? [entry.text] : [],
        status: entry.bucket === "status" ? [entry.text] : [],
      },
    });
    setActive(id);
    return;
  }

  const previousOutput = asRecord(current.output) ?? {};
  const nextText =
    entry.bucket === "tokens"
      ? `${asText(previousOutput.text)}${entry.text}`
      : asText(previousOutput.text);

  const nextReasoning = Array.isArray(previousOutput.reasoning)
    ? [...previousOutput.reasoning]
    : [];
  const nextStatus = Array.isArray(previousOutput.status)
    ? [...previousOutput.status]
    : [];

  if (entry.bucket === "reasoning") nextReasoning.push(entry.text);
  if (entry.bucket === "status") nextStatus.push(entry.text);

  upsert({
    ...current,
    timestamp: entry.timestamp,
    output: {
      ...previousOutput,
      streaming: true,
      text: nextText,
      reasoning: nextReasoning,
      status: nextStatus,
    },
  });
  setActive(current.id);
}

function addToolStep(
  kind: "tool_call" | "tool_result",
  text: string,
  payload: Record<string, unknown> | undefined,
  timestamp: number,
): void {
  const { steps, activeStepId } = useArtifactStore.getState();
  const llm = getCurrentLlmStep(steps, activeStepId);
  const toolName = asText(payload?.tool_name).trim();
  const label = toolName
    ? `Tool: ${toolName}`
    : kind === "tool_call"
      ? "Tool call"
      : "Tool result";

  add({
    id: nextId("tool"),
    type: "tool",
    label,
    parent_id: llm?.id,
    input:
      kind === "tool_call"
        ? (payload?.tool_input ?? text)
        : payload?.tool_input,
    output:
      kind === "tool_result"
        ? (payload?.tool_output ?? text)
        : payload?.tool_output,
    timestamp,
  });
}

function addTrajectoryStep(
  text: string,
  payload: Record<string, unknown> | undefined,
  timestamp: number,
): void {
  const stepData = asRecord(payload?.step_data);
  const stepIndex =
    typeof payload?.step_index === "number" ? payload.step_index : undefined;

  const inferredType: ArtifactStepType = stepData?.tool_name
    ? "tool"
    : stepData?.input || stepData?.output
      ? "repl"
      : "llm";

  add({
    id:
      asText(stepData?.id) ||
      (stepIndex != null ? `trajectory-${stepIndex}` : nextId("trajectory")),
    type: inferredType,
    label:
      asText(stepData?.label) ||
      asText(stepData?.thought) ||
      asText(stepData?.tool_name) ||
      "Trajectory step",
    input: stepData?.input,
    output: stepData?.output ?? text,
    timestamp,
  });
}

function finalizeCurrentLlm(
  text: string,
  payload: Record<string, unknown> | undefined,
  timestamp: number,
): string | undefined {
  const { steps, activeStepId } = useArtifactStore.getState();
  const llm = getCurrentLlmStep(steps, activeStepId);
  if (!llm) return undefined;

  const previousOutput = asRecord(llm.output) ?? {};
  upsert({
    ...llm,
    timestamp,
    output: {
      ...previousOutput,
      text: text || asText(previousOutput.text),
      final_reasoning: payload?.final_reasoning,
      guardrail_warnings: payload?.guardrail_warnings,
      streaming: false,
    },
  });

  return llm.id;
}

function addOutputStep(
  label: string,
  text: string,
  payload: Record<string, unknown> | undefined,
  timestamp: number,
  parentId?: string,
): void {
  const id = nextId("output");
  add({
    id,
    type: "output",
    label,
    parent_id: parentId,
    output: {
      text,
      payload,
    },
    timestamp,
  });
  setActive(id);
}

export function applyWsFrameToArtifacts(frame: WsServerMessage): void {
  if (frame.type === "error") {
    const timestamp = Date.now();
    const parentId = finalizeCurrentLlm(frame.message, undefined, timestamp);
    addOutputStep(
      "Execution error",
      frame.message,
      undefined,
      timestamp,
      parentId,
    );
    return;
  }

  const { kind, text, payload, timestamp } = frame.data;
  const epoch = toEpochMs(timestamp);

  const executionStep = normalizeExecutionStepFromPayload(payload, timestamp);
  if (executionStep) {
    upsert(executionStep);
    setActive(executionStep.id);
    return;
  }

  switch (kind) {
    case "assistant_token":
      appendIntoLlmStep({ bucket: "tokens", text, timestamp: epoch });
      return;
    case "reasoning_step":
      appendIntoLlmStep({ bucket: "reasoning", text, timestamp: epoch });
      return;
    case "status":
      appendIntoLlmStep({ bucket: "status", text, timestamp: epoch });
      return;
    case "tool_call":
    case "tool_result":
      addToolStep(kind, text, payload, epoch);
      return;
    case "trajectory_step":
      addTrajectoryStep(text, payload, epoch);
      return;
    case "final": {
      const parentId = finalizeCurrentLlm(text, payload, epoch);
      addOutputStep("Final output", text, payload, epoch, parentId);
      return;
    }
    case "cancelled": {
      const parentId = finalizeCurrentLlm(text, payload, epoch);
      addOutputStep(
        "Execution cancelled",
        text || "Request cancelled",
        payload,
        epoch,
        parentId,
      );
      return;
    }
    case "error": {
      const parentId = finalizeCurrentLlm(text, payload, epoch);
      addOutputStep(
        "Execution error",
        text || "Server error",
        payload,
        epoch,
        parentId,
      );
      return;
    }
    default:
      return;
  }
}
