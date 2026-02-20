import { mockReasoningPhase2 } from "@/lib/data/mock-skills";
import { phase2AssistantSummary, phase2ClarificationSummary } from "@/lib/skill-creation/simulation/messages";
import type {
  ClarificationFollowUpPlan,
  PhaseExecutionPlan,
} from "@/lib/skill-creation/simulation/types";

export const PHASE2_SYSTEM_MESSAGE = "Phase 2: Content Generation";

export const PHASE2_HITL = {
  question: "Review complete. Ready for validation?",
  actions: [
    { label: "Run Validation", variant: "primary" as const },
    {
      label: "Request Changes",
      variant: "secondary" as const,
    },
  ],
};

export const PHASE2_CLARIFIED_HITL = {
  question: "Content updated. Ready to validate now?",
  actions: [
    {
      label: "Run Validation",
      variant: "primary" as const,
    },
    {
      label: "Request More Changes",
      variant: "secondary" as const,
    },
  ],
};

export function buildPhase2ExecutionPlan(): PhaseExecutionPlan {
  return {
    phase: 2,
    systemMessage: PHASE2_SYSTEM_MESSAGE,
    assistantMessage: phase2AssistantSummary(),
    reasoningParts: mockReasoningPhase2.parts,
    reasoningDuration: mockReasoningPhase2.duration,
    reasoningDelayMs: 2800,
    followUpDelayMs: 400,
    followUpHitl: PHASE2_HITL,
    ensureCanvasOpen: true,
  };
}

export function buildPhase2ClarificationPlan(
  answers: string[],
): ClarificationFollowUpPlan {
  return {
    phase: 2,
    typingDelayMs: 2000,
    summaryMessage: phase2ClarificationSummary(answers),
    followUpDelayMs: 400,
    followUpHitl: PHASE2_CLARIFIED_HITL,
    ensureCanvasOpen: true,
  };
}
