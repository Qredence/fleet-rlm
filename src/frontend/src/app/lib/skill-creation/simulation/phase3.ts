import { mockReasoningPhase3 } from "../../../components/data/mock-skills";
import { phase3ValidationSummary } from "./messages";
import type { PhaseExecutionPlan } from "./types";

export const PHASE3_SYSTEM_MESSAGE = "Phase 3: Validation & Quality Assurance";

export const PHASE3_COMPLETE_SYSTEM_MESSAGE = "Skill creation complete.";

export function buildPhase3ExecutionPlan(): PhaseExecutionPlan {
  return {
    phase: 3,
    systemMessage: PHASE3_SYSTEM_MESSAGE,
    assistantMessage: phase3ValidationSummary(),
    reasoningParts: mockReasoningPhase3.parts,
    reasoningDuration: mockReasoningPhase3.duration,
    reasoningDelayMs: 2400,
    followUpDelayMs: 600,
    followUpSystemMessage: PHASE3_COMPLETE_SYSTEM_MESSAGE,
    markComplete: true,
  };
}
