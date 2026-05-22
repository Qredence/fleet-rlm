import type { WsServerMessage } from "@/lib/rlm-api";
import {
  type ArtifactActorKind,
  type ArtifactStepType,
  type ExecutionStep,
} from "@/lib/workspace/workspace-types";
import { useArtifactStore } from "@/lib/workspace/artifact-store";
import { createLocalId } from "@/lib/id";

function nextId(prefix: string): string {
  return createLocalId(prefix);
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
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

function normalizeActorKind(value: unknown): ArtifactActorKind | undefined {
  const raw = asText(value).trim().toLowerCase();
  if (raw === "root_rlm" || raw === "root-rlm" || raw === "root") return "root_rlm";
  if (raw === "sub_agent" || raw === "sub-agent" || raw === "subagent") return "sub_agent";
  if (raw === "delegate" || raw === "rlm_delegate" || raw === "rlm-delegate") return "delegate";
  if (raw === "unknown") return "unknown";
  return undefined;
}

function normalizeDepth(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.max(0, Math.trunc(value));
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return Math.max(0, Math.trunc(parsed));
  }
  return undefined;
}

function findStepById(steps: ExecutionStep[], id?: string): ExecutionStep | undefined {
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

function getAdjacentLlmStep(steps: ExecutionStep[]): ExecutionStep | undefined {
  const latest = steps[steps.length - 1];
  if (!latest || latest.type !== "llm") return undefined;

  const output = asRecord(latest.output);
  if (output?.streaming === false) return undefined;
  return latest;
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
  fallbackTimestamp: string | number | undefined,
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
  const actorKind = normalizeActorKind(step.actor_kind);
  const actorIdRaw = asText(step.actor_id).trim();
  const laneKeyRaw = asText(step.lane_key).trim();

  return {
    id,
    type,
    label,
    parent_id: parentIdRaw || undefined,
    depth: normalizeDepth(step.depth),
    actor_kind: actorKind,
    actor_id: actorIdRaw || undefined,
    lane_key: laneKeyRaw || undefined,
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

  const { steps } = useArtifactStore.getState();
  const current = getAdjacentLlmStep(steps);

  if (!current) {
    const id = nextId("llm");
    add({
      id,
      type: "llm",
      label: entry.bucket === "status" ? "Status" : "Reasoning",
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
  const nextStatus = Array.isArray(previousOutput.status) ? [...previousOutput.status] : [];

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
    addOutputStep("Execution error", frame.message, undefined, timestamp, parentId);
    return;
  }

  const { kind, text, payload, timestamp } = frame.data;
  const epoch = toEpochMs(timestamp);

  const executionStep = normalizeExecutionStepFromPayload(payload, timestamp);
  if (kind === "execution_step" && executionStep) {
    upsert(executionStep);
    setActive(executionStep.id);
    return;
  }

  switch (kind) {
    case "execution_started":
      appendIntoLlmStep({ bucket: "status", text, timestamp: epoch });
      return;
    case "execution_step":
      appendIntoLlmStep({ bucket: "status", text: text || "Execution step received", timestamp: epoch });
      return;
    case "execution_completed": {
      const parentId = finalizeCurrentLlm(text, payload, epoch);
      const summary = asRecord(payload?.run_summary ?? payload?.runSummary ?? payload?.summary);
      const cancelled = asText(summary?.status ?? payload?.status).toLowerCase() === "cancelled";
      const failed = ["failed", "error"].includes(
        asText(summary?.status ?? payload?.status).toLowerCase(),
      );
      if (failed) {
        addOutputStep("Execution error", text || "Server error", payload, epoch, parentId);
        return;
      }
      addOutputStep(cancelled ? "Execution cancelled" : "Final output", text, payload, epoch, parentId);
      return;
    }
    default:
      return;
  }
}
