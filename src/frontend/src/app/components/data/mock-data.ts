/**
 * @deprecated — Do not import from this file.
 *
 * This barrel re-export exists solely for backward compatibility.
 * All exports have been moved to focused modules:
 *
 *   ../config/typo.ts    — Typography inline-style helper (CSS variable refs)
 *   types.ts             — TypeScript types and interfaces
 *   mock-skills.ts       — Mock data, clarification questions, generated content
 *
 * Import directly from the specific module:
 *
 *   import { typo } from '../config/typo';
 *   import type { Skill } from '../data/types';
 *   import { mockSkills } from '../data/mock-skills';
 */

export { typo } from "../config/typo";
export type {
  NavItem,
  CreationPhase,
  Skill,
  TaxonomyNode,
  ChatMessage,
} from "./types";
export type { PlanStep, SkillMetadataItem } from "./types";
export {
  phase1ClarificationQuestions,
  phase2ClarificationQuestions,
  mockSkills,
  mockTaxonomy,
  analyticsData,
  generatedSkillMd,
  mockPlanSteps,
  mockSkillMetadata,
  mockReasoningPhase1,
  mockReasoningPhase2,
  mockReasoningPhase3,
} from "./mock-skills";
