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

  return Array.from(byId.values()).sort((a, b) => a.timestamp - b.timestamp);
}

export const useArtifactStore = create<ArtifactState>((set) => ({
  steps: [],
  activeStepId: undefined,
  addStep: (step) =>
    set((state) => {
      if (state.steps.some((candidate) => candidate.id === step.id)) {
        return state;
      }

      const nextSteps = dedupeSteps([...state.steps, step]);
      return {
        steps: nextSteps,
        activeStepId: state.activeStepId ?? step.id,
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

      if (idx >= 0) {
        next[idx] = step;
      } else {
        next.push(step);
      }

      const deduped = dedupeSteps(next);
      return {
        steps: deduped,
        activeStepId: state.activeStepId ?? step.id,
      };
    }),
  setActiveStepId: (id) => set({ activeStepId: id }),
  clear: () => set({ steps: [], activeStepId: undefined }),
}));
