import {
  asOptionalNumber,
  asOptionalText,
  asRecord,
} from "@/features/rlm-workspace/backendChatEventPayload";

export interface NormalizedTrajectoryStep {
  index: number;
  thought?: string;
  action?: string;
  toolName?: string;
  toolInput?: unknown;
  toolOutput?: unknown;
  label: string;
}

function trajectoryStepData(
  payload?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  const raw = payload?.step_data;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  return raw as Record<string, unknown>;
}

function normalizeOptionalUnknown(value: unknown): unknown | undefined {
  if (value == null) return undefined;
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed : undefined;
  }
  return value;
}

function parseTrajectoryStepIndex(
  payload?: Record<string, unknown>,
  stepData?: Record<string, unknown>,
): number {
  return (
    asOptionalNumber(payload?.step_index) ??
    asOptionalNumber(stepData?.index) ??
    0
  );
}

function normalizeTrajectoryStep(
  raw: Record<string, unknown>,
  index: number,
  fallbackText?: string,
): NormalizedTrajectoryStep {
  const action = asOptionalText(raw.action);
  const toolName = asOptionalText(raw.tool_name ?? raw.toolName);
  const thought = asOptionalText(raw.thought) ?? asOptionalText(fallbackText);
  const toolInput = normalizeOptionalUnknown(
    raw.tool_args ?? raw.input ?? raw.tool_input ?? raw.toolInput,
  );
  const toolOutput = normalizeOptionalUnknown(
    raw.output ?? raw.observation ?? raw.tool_output ?? raw.toolOutput,
  );
  const label =
    action ||
    (toolName ? `Tool: ${toolName}` : undefined) ||
    (thought ? `Step ${index + 1}` : undefined) ||
    `Step ${index + 1}`;

  return {
    index,
    thought,
    action,
    toolName,
    toolInput,
    toolOutput,
    label,
  };
}

function extractIndexedTrajectorySteps(
  payload?: Record<string, unknown>,
): Map<number, Record<string, unknown>> {
  const stepsByIndex = new Map<number, Record<string, unknown>>();
  if (!payload) return stepsByIndex;

  const pattern =
    /^(thought|tool_name|tool_args|tool_input|input|observation|tool_output|output|action)_(\d+)$/;
  for (const [key, value] of Object.entries(payload)) {
    const match = key.match(pattern);
    if (!match) continue;
    const field = match[1];
    if (!field) continue;
    const index = Number(match[2]);
    if (!Number.isFinite(index)) continue;
    const current = stepsByIndex.get(index) ?? {};
    current[field] = value;
    stepsByIndex.set(index, current);
  }
  return stepsByIndex;
}

export function normalizeTrajectorySteps(
  text: string,
  payload?: Record<string, unknown>,
): NormalizedTrajectoryStep[] {
  const stepsByIndex = extractIndexedTrajectorySteps(payload);
  const stepData = trajectoryStepData(payload);

  if (stepData) {
    const index = parseTrajectoryStepIndex(payload, stepData);
    const merged = {
      ...(stepsByIndex.get(index) ?? {}),
      ...stepData,
    };
    stepsByIndex.set(index, merged);
  }

  if (!stepData && stepsByIndex.size === 0 && payload) {
    const inlineStep: Record<string, unknown> = {};
    for (const field of [
      "thought",
      "action",
      "tool_name",
      "tool_args",
      "tool_input",
      "input",
      "observation",
      "tool_output",
      "output",
    ]) {
      if (payload[field] !== undefined) {
        inlineStep[field] = payload[field];
      }
    }
    if (Object.keys(inlineStep).length > 0) {
      const index = parseTrajectoryStepIndex(payload);
      stepsByIndex.set(index, inlineStep);
    }
  }

  const sorted = [...stepsByIndex.entries()]
    .sort(([left], [right]) => left - right)
    .map(([index, raw], position) =>
      normalizeTrajectoryStep(raw, index, position === 0 ? text : undefined),
    );

  if (sorted.length > 0) {
    return sorted;
  }

  const fallback = text.trim();
  if (!fallback) return [];
  const fallbackIndex = parseTrajectoryStepIndex(payload, stepData);
  return [
    {
      index: fallbackIndex,
      thought: fallback,
      label: `Step ${fallbackIndex + 1}`,
    },
  ];
}

export function normalizeTrajectoryStepsFromFinalPayload(
  payload?: Record<string, unknown>,
): NormalizedTrajectoryStep[] {
  if (!payload) return [];

  const rawTrajectory = payload.trajectory;
  if (Array.isArray(rawTrajectory)) {
    return rawTrajectory
      .map((entry, idx) => {
        const record = asRecord(entry);
        if (!record) return null;
        const index = asOptionalNumber(record.index) ?? idx;
        return normalizeTrajectoryStep(record, index);
      })
      .filter((step): step is NormalizedTrajectoryStep => step != null);
  }

  const trajectoryRecord = asRecord(rawTrajectory);
  if (trajectoryRecord) {
    return normalizeTrajectorySteps("", trajectoryRecord);
  }

  return normalizeTrajectorySteps("", payload);
}

function truncateTrajectoryDetail(value: string, maxLength = 96) {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 3)}...`;
}

function summarizeTrajectoryValue(value: unknown): string | undefined {
  if (value == null) return undefined;
  if (typeof value === "string") {
    const trimmed = value.replace(/\s+/g, " ").trim();
    return trimmed ? truncateTrajectoryDetail(trimmed) : undefined;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    const rendered = value
      .map((entry) => summarizeTrajectoryValue(entry))
      .filter((entry): entry is string => Boolean(entry));
    if (rendered.length === 0) return undefined;
    const preview = rendered.slice(0, 3).join(", ");
    return rendered.length > 3
      ? `${truncateTrajectoryDetail(preview)} (+${rendered.length - 3} more)`
      : truncateTrajectoryDetail(preview);
  }

  const record = asRecord(value);
  if (record) {
    const entries = Object.entries(record)
      .map(([key, entryValue]) => {
        const rendered = summarizeTrajectoryValue(entryValue);
        return rendered ? `${key}=${rendered}` : null;
      })
      .filter((entry): entry is string => entry != null);
    if (entries.length > 0) {
      const preview = entries.slice(0, 3).join(", ");
      return entries.length > 3
        ? `${truncateTrajectoryDetail(preview)} (+${entries.length - 3} more)`
        : truncateTrajectoryDetail(preview);
    }
  }

  try {
    return truncateTrajectoryDetail(JSON.stringify(value));
  } catch {
    return truncateTrajectoryDetail(String(value));
  }
}

export function trajectoryStepDetails(
  step: NormalizedTrajectoryStep,
): string[] {
  const details: string[] = [];
  if (step.toolName) {
    details.push(`Tool · ${step.toolName}`);
  }
  if (step.toolInput !== undefined) {
    details.push(
      `Input · ${summarizeTrajectoryValue(step.toolInput) ?? "Available"}`,
    );
  }
  if (step.toolOutput !== undefined) {
    details.push(
      `Observation · ${summarizeTrajectoryValue(step.toolOutput) ?? "Available"}`,
    );
  }
  return details;
}
