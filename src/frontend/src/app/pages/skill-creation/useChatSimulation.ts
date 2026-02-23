/**
 * useChatSimulation — encapsulates all chat-state management and mock
 * AI response simulation for the skill-creation flow.
 *
 * This hook owns:
 *   - Message array state
 *   - Input value state
 *   - Phase transitions (idle → understanding → generating → validating → complete)
 *   - Mock reasoning + streaming responses (setTimeout-based)
 *   - Clarification question flow (multi-step Q&A)
 *   - HITL resolution logic
 *
 * Prompt feature state (activeFeatures, mode, selectedSkills) lives in
 * NavigationContext and is consumed by SkillCreationFlow directly — this
 * hook no longer owns or proxies that state.
 *
 * Session reset:
 *   NavigationContext exposes a monotonically increasing `sessionId`.
 *   When `newSession()` is called, `sessionId` increments and this hook
 *   reacts by clearing all local state and cancelling pending timers.
 *
 * The setTimeout-based mock logic is isolated here so it can be trivially
 * replaced with a real API client (e.g. streaming fetch) in the future.
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { toast } from "sonner";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { useNavigation } from "@/hooks/useNavigation";
import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import type { Conversation } from "@/hooks/useChatHistory";
import { createLocalId } from "@/lib/id";
import {
  phase1ClarificationQuestions,
  phase2ClarificationQuestions,
} from "@/lib/data/mock-skills";
import {
  buildPhase1ClarificationPlan,
  buildPhase1ExecutionPlan,
} from "@/lib/skill-creation/simulation/phase1";
import {
  buildPhase2ClarificationPlan,
  buildPhase2ExecutionPlan,
} from "@/lib/skill-creation/simulation/phase2";
import {
  buildPhase3ExecutionPlan,
} from "@/lib/skill-creation/simulation/phase3";
import { clarificationIntro } from "@/lib/skill-creation/simulation/messages";
import type {
  ClarificationFollowUpPlan,
  PhaseExecutionPlan,
} from "@/lib/skill-creation/simulation/types";

// ── Return type ─────────────────────────────────────────────────────

export interface ChatSimulation {
  messages: ChatMessage[];
  inputValue: string;
  setInputValue: (v: string) => void;
  phase: CreationPhase;
  isTyping: boolean;
  handleSubmit: () => void;
  resolveHitl: (msgId: string, actionLabel: string) => void;
  resolveClarification: (msgId: string, answer: string) => void;
  /** Load a previously saved conversation into the chat. */
  loadConversation: (conversation: Conversation) => void;
}

// ── Hook ────────────────────────────────────────────────────────────

export function useChatSimulation(): ChatSimulation {
  const { isCanvasOpen, openCanvas, setCreationPhase, sessionId } =
    useNavigation();
  const telemetry = useTelemetry();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [phase, setPhase] = useState<CreationPhase>("idle");
  const [isTyping, setIsTyping] = useState(false);

  // ── Timer management ────────────────────────────────────────────
  // All timeouts go through `safeTimeout` so we can cancel them on
  // session reset or unmount. This prevents stale callbacks from
  // firing into a cleared message list.

  const pendingTimers = useRef(new Set<ReturnType<typeof setTimeout>>());

  const safeTimeout = useCallback((fn: () => void, ms: number) => {
    const id = setTimeout(() => {
      pendingTimers.current.delete(id);
      fn();
    }, ms);
    pendingTimers.current.add(id);
  }, []);

  const clearPendingTimers = useCallback(() => {
    pendingTimers.current.forEach(clearTimeout);
    pendingTimers.current.clear();
  }, []);

  // Cancel timers on unmount
  useEffect(() => {
    return () => clearPendingTimers();
  }, [clearPendingTimers]);

  // ── Session reset ───────────────────────────────────────────────
  // When NavigationContext increments sessionId (via newSession()),
  // wipe all local state and cancel any in-flight mock responses.

  const isFirstMount = useRef(true);

  useEffect(() => {
    if (isFirstMount.current) {
      isFirstMount.current = false;
      return; // Skip the initial mount — state is already fresh
    }
    clearPendingTimers();
    setMessages([]);
    setInputValue("");
    setPhase("idle");
    setIsTyping(false);
    clarificationRef.current = null;
  }, [sessionId, clearPendingTimers]);

  // ── Clarification flow tracker ──────────────────────────────────
  const clarificationRef = useRef<{
    phaseNum: 1 | 2;
    step: number;
    totalSteps: number;
    answers: string[];
  } | null>(null);

  // ── Helpers ─────────────────────────────────────────────────────

  const updatePhase = useCallback(
    (p: CreationPhase) => {
      setPhase(p);
      setCreationPhase(p);

      switch (p) {
        case "understanding":
          toast("Phase 1 started", {
            description: "Analyzing your request and planning the skill.",
          });
          break;
        case "generating":
          toast.success("Plan approved", {
            description: "Generating skill content and documentation.",
          });
          break;
        case "validating":
          toast("Running validation", {
            description: "Checking compliance and quality metrics.",
          });
          break;
        case "complete":
          toast.success("Skill created successfully", {
            description: "Registered in the taxonomy and ready to use.",
          });
          break;
      }
    },
    [setCreationPhase],
  );

  const addMessage = useCallback((msg: Omit<ChatMessage, "id">) => {
    setMessages((prev) => [
      ...prev,
      {
        ...msg,
        id: createLocalId("msg"),
      },
    ]);
  }, []);

  const maybeOpenCanvas = useCallback(
    (shouldOpen?: boolean) => {
      if (shouldOpen && !isCanvasOpen) {
        openCanvas();
      }
    },
    [isCanvasOpen, openCanvas],
  );

  const runPhaseExecutionPlan = useCallback(
    (plan: PhaseExecutionPlan) => {
      addMessage({
        type: "system",
        content: plan.systemMessage,
        phase: plan.phase,
      });

      const reasoningId = createLocalId(`reason${plan.phase}`);
      setMessages((prev) => [
        ...prev,
        {
          id: reasoningId,
          type: "reasoning",
          content: "",
          phase: plan.phase,
          reasoningData: {
            parts: plan.reasoningParts,
            isThinking: true,
          },
        },
      ]);

      setIsTyping(true);

      safeTimeout(() => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === reasoningId
              ? {
                  ...m,
                  reasoningData: {
                    parts: plan.reasoningParts,
                    isThinking: false,
                    duration: plan.reasoningDuration,
                  },
                }
              : m,
          ),
        );

        setIsTyping(false);
        addMessage({
          type: "assistant",
          phase: plan.phase,
          streaming: true,
          content: plan.assistantMessage,
        });

        maybeOpenCanvas(plan.ensureCanvasOpen);

        if (!plan.followUpHitl && !plan.followUpSystemMessage && !plan.markComplete) {
          return;
        }

        safeTimeout(() => {
          if (plan.followUpHitl) {
            addMessage({
              type: "hitl",
              phase: plan.phase,
              content: "",
              hitlData: plan.followUpHitl,
            });
          }

          if (plan.followUpSystemMessage) {
            addMessage({
              type: "system",
              phase: plan.phase,
              content: plan.followUpSystemMessage,
            });
          }

          if (plan.markComplete) {
            updatePhase("complete");
          }
        }, plan.followUpDelayMs ?? 0);
      }, plan.reasoningDelayMs);
    },
    [addMessage, maybeOpenCanvas, safeTimeout, updatePhase],
  );

  const runClarificationPlan = useCallback(
    (plan: ClarificationFollowUpPlan) => {
      setIsTyping(true);

      safeTimeout(() => {
        setIsTyping(false);
        addMessage({
          type: "assistant",
          phase: plan.phase,
          content: plan.summaryMessage,
        });

        maybeOpenCanvas(plan.ensureCanvasOpen);

        safeTimeout(() => {
          addMessage({
            type: "hitl",
            phase: plan.phase,
            content: "",
            hitlData: plan.followUpHitl,
          });
        }, plan.followUpDelayMs);
      }, plan.typingDelayMs);
    },
    [addMessage, maybeOpenCanvas, safeTimeout],
  );

  // ── Clarification helpers ───────────────────────────────────────

  const addClarificationQuestion = useCallback(
    (phaseNum: 1 | 2, stepIndex: number) => {
      const questions =
        phaseNum === 1
          ? phase1ClarificationQuestions
          : phase2ClarificationQuestions;
      const q = questions[stepIndex];
      if (!q) return;
      addMessage({
        type: "clarification",
        content: "",
        phase: phaseNum,
        clarificationData: {
          question: q.question,
          stepLabel: `Question ${stepIndex + 1} of ${questions.length}`,
          options: q.options,
          customOptionId: q.customOptionId,
        },
      });
    },
    [addMessage],
  );

  const startClarification = useCallback(
    (phaseNum: 1 | 2) => {
      const questions =
        phaseNum === 1
          ? phase1ClarificationQuestions
          : phase2ClarificationQuestions;
      clarificationRef.current = {
        phaseNum,
        step: 0,
        totalSteps: questions.length,
        answers: [],
      };
      addMessage({
        type: "assistant",
        content: clarificationIntro(phaseNum),
        phase: phaseNum,
      });
      safeTimeout(() => addClarificationQuestion(phaseNum, 0), 400);
    },
    [addMessage, addClarificationQuestion, safeTimeout],
  );

  const runClarificationFollowUp = useCallback(
    (phaseNum: 1 | 2, answers: string[]) => {
      const plan =
        phaseNum === 1
          ? buildPhase1ClarificationPlan(answers)
          : buildPhase2ClarificationPlan(answers);
      runClarificationPlan(plan);
    },
    [runClarificationPlan],
  );

  const resolveClarification = useCallback(
    (msgId: string, answer: string) => {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msgId && m.clarificationData
            ? {
                ...m,
                clarificationData: {
                  ...m.clarificationData,
                  resolved: true,
                  resolvedAnswer: answer,
                },
              }
            : m,
        ),
      );
      const ctx = clarificationRef.current;
      if (!ctx) return;
      ctx.answers.push(answer);
      ctx.step += 1;
      if (ctx.step < ctx.totalSteps) {
        safeTimeout(
          () => addClarificationQuestion(ctx.phaseNum, ctx.step),
          500,
        );
      } else {
        safeTimeout(() => {
          runClarificationFollowUp(ctx.phaseNum, ctx.answers);
          clarificationRef.current = null;
        }, 400);
      }
    },
    [addClarificationQuestion, runClarificationFollowUp, safeTimeout],
  );

  // ── Phase simulations ───────────────────────────────────────────

  const runPhase1 = useCallback(
    (userTask: string) => {
      updatePhase("understanding");
      runPhaseExecutionPlan(buildPhase1ExecutionPlan(userTask));
    },
    [runPhaseExecutionPlan, updatePhase],
  );

  const runPhase2 = useCallback(() => {
    updatePhase("generating");
    runPhaseExecutionPlan(buildPhase2ExecutionPlan());
  }, [runPhaseExecutionPlan, updatePhase]);

  const runPhase3 = useCallback(() => {
    updatePhase("validating");
    runPhaseExecutionPlan(buildPhase3ExecutionPlan());
  }, [runPhaseExecutionPlan, updatePhase]);

  // ── HITL resolution ─────────────────────────────────────────────

  const resolveHitl = useCallback(
    (msgId: string, actionLabel: string) => {
      // PostHog: Capture HITL checkpoint resolution
      telemetry.capture("hitl_checkpoint_resolved", {
        checkpoint_phase: phase,
        action_label: actionLabel,
        is_approval: actionLabel.includes("Approve") || actionLabel.includes("Validation") || actionLabel.includes("Run"),
      });

      setMessages((prev) =>
        prev.map((m) =>
          m.id === msgId && m.hitlData
            ? {
                ...m,
                hitlData: {
                  ...m.hitlData,
                  resolved: true,
                  resolvedLabel: actionLabel,
                },
              }
            : m,
        ),
      );
      if (phase === "understanding") {
        if (actionLabel.includes("Approve")) {
          runPhase2();
        } else {
          startClarification(1);
        }
      } else if (phase === "generating") {
        if (
          actionLabel.includes("Validation") ||
          actionLabel.includes("Run")
        ) {
          runPhase3();
        } else {
          startClarification(2);
        }
      }
    },
    [phase, runPhase2, runPhase3, startClarification, telemetry],
  );

  // ── Submit ──────────────────────────────────────────────────────

  const handleSubmit = useCallback(() => {
    if (!inputValue.trim()) return;
    const text = inputValue.trim();
    setInputValue("");
    addMessage({ type: "user", content: text });
    if (phase === "idle") runPhase1(text);
  }, [inputValue, phase, addMessage, runPhase1]);

  // ── Load conversation ───────────────────────────────────────────

  const loadConversation = useCallback(
    (conversation: Conversation) => {
      clearPendingTimers();
      clarificationRef.current = null;
      setMessages(conversation.messages);
      setInputValue("");
      setPhase(conversation.phase);
      setCreationPhase(conversation.phase);
      setIsTyping(false);
    },
    [clearPendingTimers, setCreationPhase],
  );

  // ── Public API ──────────────────────────────────────────────────

  return {
    messages,
    inputValue,
    setInputValue,
    phase,
    isTyping,
    handleSubmit,
    resolveHitl,
    resolveClarification,
    loadConversation,
  };
}
