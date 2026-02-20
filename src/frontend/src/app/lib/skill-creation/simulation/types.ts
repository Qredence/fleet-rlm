import type { ChatMessage } from "../../../components/data/types";

export type PhaseNumber = 1 | 2 | 3;

export type HitlData = NonNullable<ChatMessage["hitlData"]>;

export type ReasoningPart = NonNullable<
  ChatMessage["reasoningData"]
>["parts"][number];

export interface PhaseExecutionPlan {
  phase: PhaseNumber;
  systemMessage: string;
  assistantMessage: string;
  reasoningParts: ReasoningPart[];
  reasoningDuration?: number;
  reasoningDelayMs: number;
  followUpDelayMs?: number;
  followUpHitl?: HitlData;
  followUpSystemMessage?: string;
  ensureCanvasOpen?: boolean;
  markComplete?: boolean;
}

export interface ClarificationFollowUpPlan {
  phase: 1 | 2;
  typingDelayMs: number;
  summaryMessage: string;
  followUpDelayMs: number;
  followUpHitl: HitlData;
  ensureCanvasOpen: boolean;
}
