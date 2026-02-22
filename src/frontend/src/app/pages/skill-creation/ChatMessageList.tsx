import { type RefObject, type ReactNode } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import type { ChatMessage } from "@/lib/data/types";
import { typo } from "@/lib/config/typo";
import { cn } from "@/components/ui/utils";
import { Reasoning } from "@/components/ui/reasoning";
import { ClarificationCard } from "@/features/ClarificationCard";
import { TypingDots } from "@/components/shared/TypingDots";
import { SuggestionChip } from "@/components/ui/suggestion-chip";
import {
  SuggestionIconBolt,
  SuggestionIconTune,
  SuggestionIconSparkle,
} from "@/components/shared/SuggestionIcons";
import {
  fadeUp,
  fadeUpReduced,
} from "@/app/pages/skill-creation/animation-presets";
import { AssistantMessage } from "@/app/pages/skill-creation/AssistantMessage";
import { UserMessage } from "@/app/pages/skill-creation/UserMessage";
import { HitlCard } from "@/app/pages/skill-creation/HitlCard";
import { Clock } from "lucide-react";

// ── Suggestion prompts shown in the welcome state ────────────────────
const suggestions = [
  {
    text: "Analyze a codebase and extract its architecture",
    Icon: SuggestionIconBolt,
  },
  {
    text: "Summarize this document and find key insights",
    Icon: SuggestionIconTune,
  },
  {
    text: "Write and execute a Python script for me",
    Icon: SuggestionIconSparkle,
  },
];

// ── Props ────────────────────────────────────────────────────────────

interface ChatMessageListProps {
  messages: ChatMessage[];
  isTyping: boolean;
  isMobile: boolean;
  scrollRef: RefObject<HTMLDivElement | null>;
  contentRef: RefObject<HTMLDivElement | null>;
  onSuggestionClick: (text: string) => void;
  onResolveHitl: (msgId: string, label: string) => void;
  onResolveClarification: (msgId: string, answer: string) => void;
  /** Whether the history panel is currently shown */
  showHistory?: boolean;
  /** Toggle the history panel */
  onToggleHistory?: () => void;
  /** Whether there are past conversations available */
  hasHistory?: boolean;
  /** The history panel ReactNode to render below the welcome hero */
  historyPanel?: ReactNode;
}

// ── Component ────────────────────────────────────────────────────────

/**
 * ChatMessageList — renders the full chat viewport:
 *   • Welcome hero + suggestion pills when the conversation is empty
 *   • All message types (system, user, assistant, hitl, clarification, reasoning)
 *   • Typing indicator dots
 */
export function ChatMessageList({
  messages,
  isTyping,
  isMobile,
  scrollRef,
  contentRef,
  onSuggestionClick,
  onResolveHitl,
  onResolveClarification,
  showHistory,
  onToggleHistory,
  hasHistory,
  historyPanel,
}: ChatMessageListProps) {
  const prefersReduced = useReducedMotion();
  const preset = prefersReduced ? fadeUpReduced : fadeUp;

  return (
    <div
      ref={scrollRef}
      className="flex-1 min-h-0 overflow-y-auto"
      style={{ overscrollBehavior: "contain" }}
    >
      <div ref={contentRef} className="px-4 md:px-6 py-6 md:py-8">
        <div className="max-w-[800px] w-full mx-auto space-y-6">
          {/* ── Welcome state ─────────────────────────────────────── */}
          {messages.length === 0 && (
            <motion.div
              {...preset}
              className={cn(
                "flex flex-col items-center justify-center pt-16 pb-8",
                isMobile && "pt-10",
              )}
            >
              <div className="flex flex-col justify-center pb-[5px] w-full mb-10">
                <h2
                  className="text-foreground w-full"
                  style={{
                    ...typo.display,
                    fontWeight: "var(--font-weight-medium)",
                    lineHeight: "40px",
                    letterSpacing: "-0.53px",
                    textWrap: "balance",
                  }}
                >
                  Agentic Fleet Session
                </h2>
                <p
                  className="text-muted-foreground w-full"
                  style={{
                    ...typo.display,
                    fontWeight: "var(--font-weight-regular)",
                    letterSpacing: "-1.6px",
                    textWrap: "balance",
                  }}
                >
                  What do you need ?
                </p>
              </div>

              <div
                className="flex flex-wrap items-center justify-start gap-3 w-full"
                aria-live="polite"
                aria-label="Suggestion actions"
              >
                {suggestions.map((s, i) => {
                  return (
                    <SuggestionChip
                      key={s.text}
                      icon={s.Icon}
                      label={s.text}
                      index={i}
                      onClick={onSuggestionClick}
                    />
                  );
                })}
              </div>

              {/* History toggle — shown only when past conversations exist */}
              {hasHistory && !showHistory && (
                <motion.div
                  className="w-full mt-6"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={
                    prefersReduced ? { duration: 0.01 } : { delay: 0.35 }
                  }
                >
                  <button
                    type="button"
                    className="flex items-center gap-2 px-4 py-2.5 rounded-button border border-border-subtle hover:border-border-strong hover:bg-secondary/60 transition-colors"
                    onClick={onToggleHistory}
                  >
                    <Clock
                      className="size-4 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <span className="text-muted-foreground" style={typo.label}>
                      View recent conversations
                    </span>
                  </button>
                </motion.div>
              )}
            </motion.div>
          )}

          {/* ── History panel (rendered when open in welcome state) ── */}
          {messages.length === 0 && (
            <AnimatePresence>{historyPanel}</AnimatePresence>
          )}

          {/* ── Message list ───────────────────────────────────────── */}
          {messages.map((msg) => (
            <motion.div key={msg.id} {...preset}>
              {msg.type === "system" && (
                <div className="flex items-center gap-4 py-6">
                  <div className="h-[0.5px] flex-1 bg-border-strong opacity-20" />
                  <span
                    className="text-muted-foreground shrink-0 uppercase tracking-[0.2em] opacity-40"
                    style={{
                      ...typo.micro,
                      fontWeight: "var(--font-weight-semibold)",
                    }}
                  >
                    {msg.content}
                  </span>
                  <div className="h-[0.5px] flex-1 bg-border-strong opacity-20" />
                </div>
              )}

              {msg.type === "user" && <UserMessage content={msg.content} />}

              {msg.type === "assistant" && (
                <div className="mb-6">
                  <AssistantMessage
                    content={msg.content}
                    streaming={msg.streaming}
                  />
                </div>
              )}

              {msg.type === "hitl" && msg.hitlData && (
                <div className="mb-8">
                  <HitlCard
                    data={msg.hitlData}
                    onResolve={(label) => onResolveHitl(msg.id, label)}
                  />
                </div>
              )}

              {msg.type === "clarification" && msg.clarificationData && (
                <div className="mb-8">
                  <ClarificationCard
                    data={msg.clarificationData}
                    onResolve={(answer) =>
                      onResolveClarification(msg.id, answer)
                    }
                  />
                </div>
              )}

              {msg.type === "reasoning" && msg.reasoningData && (
                <div className="mb-4">
                  <Reasoning
                    parts={msg.reasoningData.parts}
                    isThinking={msg.reasoningData.isThinking}
                    duration={msg.reasoningData.duration}
                  />
                </div>
              )}

              {msg.type === "plan_update" && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 p-3 rounded-lg bg-accent/5 border border-accent/20">
                    <div className="size-2 rounded-full bg-accent animate-pulse" />
                    <span className="text-accent" style={typo.label}>
                      {msg.content}
                    </span>
                  </div>
                </div>
              )}

              {msg.type === "rlm_executing" && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 p-3 rounded-lg bg-primary/5 border border-primary/20">
                    <div className="size-2 rounded-full bg-primary animate-pulse" />
                    <span className="text-primary" style={typo.label}>
                      {msg.content}
                    </span>
                  </div>
                </div>
              )}

              {msg.type === "memory_update" && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 p-3 rounded-lg bg-green-500/5 border border-green-500/20">
                    <div className="size-2 rounded-full bg-green-500 animate-pulse" />
                    <span className="text-green-500" style={typo.label}>
                      {msg.content}
                    </span>
                  </div>
                </div>
              )}
            </motion.div>
          ))}

          {/* ── Typing indicator ───────────────────────────────────── */}
          {isTyping && (
            <div className="pt-2">
              <TypingDots />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
