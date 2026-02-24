import { mockReasoningPhase1 } from "@/lib/data/mock-skills";
import {
  phase1AssistantSummary,
  phase1ClarificationSummary,
} from "@/lib/skill-creation/simulation/messages";
import type {
  ClarificationFollowUpPlan,
  PhaseExecutionPlan,
} from "@/lib/skill-creation/simulation/types";

export const PHASE1_SYSTEM_MESSAGE = "Phase 1: Understanding & Planning";

export const PHASE1_HITL = {
  question: "Does this plan align with your requirements?",
  actions: [
    {
      label: "Approve & Continue",
      variant: "primary" as const,
    },
    { label: "Clarify", variant: "secondary" as const },
  ],
};

export const PHASE1_CLARIFIED_HITL = {
  question: "Approve the revised plan?",
  actions: [
    {
      label: "Approve & Continue",
      variant: "primary" as const,
    },
    {
      label: "Clarify Further",
      variant: "secondary" as const,
    },
  ],
};

export function buildPhase1ExecutionPlan(userTask: string): PhaseExecutionPlan {
  return {
    phase: 1,
    systemMessage: PHASE1_SYSTEM_MESSAGE,
    assistantMessage: phase1AssistantSummary(userTask),
    reasoningParts: mockReasoningPhase1.parts,
    reasoningDuration: mockReasoningPhase1.duration,
    reasoningDelayMs: 2200,
    followUpDelayMs: 400,
    followUpHitl: PHASE1_HITL,
  };
}

export function buildPhase1ClarificationPlan(
  answers: string[],
): ClarificationFollowUpPlan {
  return {
    phase: 1,
    typingDelayMs: 1800,
    summaryMessage: phase1ClarificationSummary(answers),
    followUpDelayMs: 400,
    followUpHitl: PHASE1_CLARIFIED_HITL,
    ensureCanvasOpen: false,
  };
}
