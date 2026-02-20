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
import { usePostHog } from "@posthog/react";
import { useNavigation } from "../../components/hooks/useNavigation";
import type { ChatMessage, CreationPhase } from "../../components/data/types";
import type { Conversation } from "../../components/hooks/useChatHistory";
import { createLocalId } from "../../lib/id";
import {
  phase1ClarificationQuestions,
  phase2ClarificationQuestions,
  mockReasoningPhase1,
  mockReasoningPhase2,
  mockReasoningPhase3,
} from "../../components/data/mock-skills";

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
  const posthog = usePostHog();
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
        content:
          phaseNum === 1
            ? "I have a few questions to refine the plan. This helps me generate a more targeted skill."
            : "Let me understand what changes you need. A couple of quick questions:",
        phase: phaseNum,
      });
      safeTimeout(() => addClarificationQuestion(phaseNum, 0), 400);
    },
    [addMessage, addClarificationQuestion, safeTimeout],
  );

  const runClarificationFollowUp = useCallback(
    (phaseNum: 1 | 2, answers: string[]) => {
      setIsTyping(true);
      if (phaseNum === 1) {
        safeTimeout(() => {
          setIsTyping(false);
          addMessage({
            type: "assistant",
            phase: 1,
            content: `Thanks for clarifying! I've refined the plan based on your inputs:\n\n**Updated Scope:** ${answers[0]}\n**Language Support:** ${answers[1]}\n**Coverage Model:** ${answers[2]}\n\n**Revised Intent Analysis:**\n• **Purpose:** Automated test suite generation\n• **Problem:** Manual testing cannot keep up with CI/CD velocity\n• **Value:** Reduce testing overhead by 70% with targeted coverage`,
          });
          safeTimeout(() => {
            addMessage({
              type: "hitl",
              phase: 1,
              content: "",
              hitlData: {
                question: "Approve the revised plan?",
                actions: [
                  {
                    label: "Approve & Continue",
                    variant: "primary",
                  },
                  {
                    label: "Clarify Further",
                    variant: "secondary",
                  },
                ],
              },
            });
          }, 400);
        }, 1800);
      } else {
        safeTimeout(() => {
          setIsTyping(false);
          addMessage({
            type: "assistant",
            phase: 2,
            content: `I've updated the generated content based on your feedback:\n\n• **Changes applied:** ${answers[0]}\n• **Format updates:** ${answers[1]}\n\nThe canvas panel now reflects these changes.`,
          });
          if (!isCanvasOpen) openCanvas();
          safeTimeout(() => {
            addMessage({
              type: "hitl",
              phase: 2,
              content: "",
              hitlData: {
                question: "Content updated. Ready to validate now?",
                actions: [
                  {
                    label: "Run Validation",
                    variant: "primary",
                  },
                  {
                    label: "Request More Changes",
                    variant: "secondary",
                  },
                ],
              },
            });
          }, 400);
        }, 2000);
      }
    },
    [addMessage, isCanvasOpen, openCanvas, safeTimeout],
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
      addMessage({
        type: "system",
        content: "Phase 1: Understanding & Planning",
        phase: 1,
      });
      const reasoningId = `msg-${Date.now()}-reason1`;
      setMessages((prev) => [
        ...prev,
        {
          id: reasoningId,
          type: "reasoning" as const,
          content: "",
          phase: 1,
          reasoningData: {
            parts: mockReasoningPhase1.parts,
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
                    parts: mockReasoningPhase1.parts,
                    isThinking: false,
                    duration: mockReasoningPhase1.duration,
                  },
                }
              : m,
          ),
        );
        setIsTyping(false);
        addMessage({
          type: "assistant",
          phase: 1,
          streaming: true,
          content: `I've analyzed your request and identified the following:\n\n**Domain:** Development\n**Category:** Testing / Quality Assurance\n\n**Intent Analysis:**\n• **Purpose:** ${userTask}\n• **Problem:** Manual test writing is time-consuming and often incomplete\n• **Value:** Reduce testing overhead by 60% while improving coverage\n\n**Suggested Taxonomy Path:**\n\`/development/testing/test-generation\``,
        });
        safeTimeout(() => {
          addMessage({
            type: "hitl",
            phase: 1,
            content: "",
            hitlData: {
              question: "Does this plan align with your requirements?",
              actions: [
                {
                  label: "Approve & Continue",
                  variant: "primary",
                },
                { label: "Clarify", variant: "secondary" },
              ],
            },
          });
        }, 400);
      }, 2200);
    },
    [addMessage, updatePhase, safeTimeout],
  );

  const runPhase2 = useCallback(() => {
    updatePhase("generating");
    addMessage({
      type: "system",
      content: "Phase 2: Content Generation",
      phase: 2,
    });
    const reasoningId = `msg-${Date.now()}-reason2`;
    setMessages((prev) => [
      ...prev,
      {
        id: reasoningId,
        type: "reasoning" as const,
        content: "",
        phase: 2,
        reasoningData: {
          parts: mockReasoningPhase2.parts,
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
                  parts: mockReasoningPhase2.parts,
                  isThinking: false,
                  duration: mockReasoningPhase2.duration,
                },
              }
            : m,
        ),
      );
      setIsTyping(false);
      addMessage({
        type: "assistant",
        phase: 2,
        streaming: true,
        content:
          "Content generation complete. I've created the full documentation and working demonstrations in the canvas.",
      });
      if (!isCanvasOpen) openCanvas();
      safeTimeout(() => {
        addMessage({
          type: "hitl",
          phase: 2,
          content: "",
          hitlData: {
            question: "Review complete. Ready for validation?",
            actions: [
              { label: "Run Validation", variant: "primary" },
              {
                label: "Request Changes",
                variant: "secondary",
              },
            ],
          },
        });
      }, 400);
    }, 2800);
  }, [addMessage, updatePhase, isCanvasOpen, openCanvas, safeTimeout]);

  const runPhase3 = useCallback(() => {
    updatePhase("validating");
    addMessage({
      type: "system",
      content: "Phase 3: Validation & Quality Assurance",
      phase: 3,
    });
    const reasoningId = `msg-${Date.now()}-reason3`;
    setMessages((prev) => [
      ...prev,
      {
        id: reasoningId,
        type: "reasoning" as const,
        content: "",
        phase: 3,
        reasoningData: {
          parts: mockReasoningPhase3.parts,
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
                  parts: mockReasoningPhase3.parts,
                  isThinking: false,
                  duration: mockReasoningPhase3.duration,
                },
              }
            : m,
        ),
      );
      setIsTyping(false);
      addMessage({
        type: "assistant",
        phase: 3,
        streaming: true,
        content:
          "**Validation Results:**\n• Compliance: Passed\n• Quality Score: 94/100\n• Edge Cases: 100% verified\n\nSkill successfully registered.",
      });
      safeTimeout(() => {
        addMessage({
          type: "system",
          content: "Skill creation complete.",
          phase: 3,
        });
        updatePhase("complete");
      }, 600);
    }, 2400);
  }, [addMessage, updatePhase, safeTimeout]);

  // ── HITL resolution ─────────────────────────────────────────────

  const resolveHitl = useCallback(
    (msgId: string, actionLabel: string) => {
      // PostHog: Capture HITL checkpoint resolution
      posthog?.capture("hitl_checkpoint_resolved", {
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
    [phase, runPhase2, runPhase3, startClarification, posthog],
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
