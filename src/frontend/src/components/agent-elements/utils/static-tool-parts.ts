import type { StepState, TimelineStep } from "../types/timeline";

export type SyntheticToolPartState =
  | "input-streaming"
  | "call"
  | "output-available"
  | "output-error";

export type SyntheticToolPart = {
  type: string;
  toolCallId: string;
  state: SyntheticToolPartState;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  startedAt?: number;
};

export function buildThinkingStep(
  id: string,
  text: string,
  options?: { toolName?: string; duration?: number },
): Extract<TimelineStep, { type: "tool-call" }> {
  return {
    id,
    type: "tool-call",
    toolName: options?.toolName ?? "Thinking",
    toolDetail: "",
    duration: options?.duration ?? 0,
    toolVariant: "thinking",
    thoughtContent: text,
  };
}

export function buildSyntheticToolPart(
  type: string,
  toolCallId: string,
  input: Record<string, unknown>,
  output: Record<string, unknown> | undefined,
  state: SyntheticToolPartState,
  options?: { startedAt?: number },
): SyntheticToolPart {
  return {
    type,
    toolCallId,
    state,
    input,
    ...(output ? { output } : {}),
    ...(options?.startedAt ? { startedAt: options.startedAt } : {}),
  };
}

export function mapSyntheticStateToStepState(state: SyntheticToolPartState): StepState {
  if (state === "output-available" || state === "output-error") return "complete";
  return "animating";
}
