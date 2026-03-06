import { create } from "zustand";

export type ArtifactStepType = "llm" | "repl" | "tool" | "memory" | "output";
export type ArtifactActorKind =
  | "root_rlm"
  | "sub_agent"
  | "delegate"
  | "unknown";

export interface ExecutionStep {
  id: string;
  parent_id?: string;
  sequence?: number;
  type: ArtifactStepType;
  label: string;
  depth?: number | null;
  actor_kind?: ArtifactActorKind | null;
  actor_id?: string | null;
  lane_key?: string | null;
  input?: unknown;
  output?: unknown;
  timestamp: number;
}

interface ArtifactState {
  steps: ExecutionStep[];
  activeStepId?: string;
  addStep: (step: ExecutionStep) => void;
  setSteps: (steps: ExecutionStep[]) => void;
  upsertStep: (step: ExecutionStep) => void;
  setActiveStepId: (id?: string) => void;
  clear: () => void;
}

function dedupeSteps(steps: ExecutionStep[]): ExecutionStep[] {
  const byId = new Map<string, ExecutionStep>();

  for (const step of steps) {
    byId.set(step.id, step);
  }

  return Array.from(byId.values()).sort((a, b) => {
    const aSeq = a.sequence;
    const bSeq = b.sequence;
    if (aSeq != null && bSeq != null && aSeq !== bSeq) return aSeq - bSeq;
    if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp;
    return a.id.localeCompare(b.id);
  });
}

function nextSequence(steps: ExecutionStep[]): number {
  return (
    steps.reduce((max, step) => {
      const candidate = step.sequence ?? 0;
      return candidate > max ? candidate : max;
    }, 0) + 1
  );
}

export const useArtifactStore = create<ArtifactState>((set) => ({
  steps: [],
  activeStepId: undefined,
  addStep: (step) =>
    set((state) => {
      if (state.steps.some((candidate) => candidate.id === step.id)) {
        return state;
      }

      const normalizedStep: ExecutionStep = {
        ...step,
        sequence: step.sequence ?? nextSequence(state.steps),
      };
      const nextSteps = dedupeSteps([...state.steps, normalizedStep]);
      return {
        steps: nextSteps,
        activeStepId: state.activeStepId ?? normalizedStep.id,
      };
    }),
  setSteps: (steps) =>
    set({
      steps: dedupeSteps(steps),
    }),
  upsertStep: (step) =>
    set((state) => {
      const next = [...state.steps];
      const idx = next.findIndex((candidate) => candidate.id === step.id);
      const existingSequence = idx >= 0 ? next[idx]?.sequence : undefined;
      const normalizedStep: ExecutionStep = {
        ...step,
        sequence:
          step.sequence ?? existingSequence ?? nextSequence(state.steps),
      };

      if (idx >= 0) {
        next[idx] = normalizedStep;
      } else {
        next.push(normalizedStep);
      }

      const deduped = dedupeSteps(next);
      return {
        steps: deduped,
        activeStepId: state.activeStepId ?? normalizedStep.id,
      };
    }),
  setActiveStepId: (id) => set({ activeStepId: id }),
  clear: () => set({ steps: [], activeStepId: undefined }),
}));
