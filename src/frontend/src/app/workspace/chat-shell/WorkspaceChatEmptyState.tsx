import { FileText, ImagePlay, PenLine, type LucideIcon } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useCallback } from "react";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { cn } from "@/lib/utils";

type Suggestion = {
  label: string;
  prompt: string;
  Icon?: LucideIcon;
  accentClassName?: string;
};

const suggestions: readonly Suggestion[] = [
  {
    label: "Help me write",
    prompt: "Help me write",
    Icon: PenLine,
    accentClassName: "text-fuchsia-500",
  },
  {
    label: "Summarize text",
    prompt: "Summarize this text",
    Icon: FileText,
    accentClassName: "text-amber-500",
  },
  {
    label: "Analyze image",
    prompt: "Analyze this image",
    Icon: ImagePlay,
    accentClassName: "text-indigo-500",
  },
  {
    label: "More",
    prompt: "Show me more ideas for tasks",
  },
] as const;

interface WorkspaceChatEmptyStateProps {
  isMobile: boolean;
  onSuggestionClick: (text: string) => void;
}

export function WorkspaceChatEmptyState({
  isMobile,
  onSuggestionClick,
}: WorkspaceChatEmptyStateProps) {
  const prefersReduced = useReducedMotion();

  return (
    <ConversationEmptyState
      icon={null}
      className={cn(
        "h-auto w-full items-center justify-center gap-0 px-0 pb-2 text-center",
        isMobile ? "pt-6" : "pt-0",
      )}
    >
      <motion.div
        initial={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={
          prefersReduced
            ? { duration: 0.01 }
            : { duration: 0.28, ease: "easeOut" }
        }
        className="flex w-full max-w-3xl flex-col items-center gap-3"
      >
        <div className="flex w-full flex-col items-center gap-[3px]">
          <h2
            className={cn(
              "font-medium tracking-[-0.02em] text-foreground",
              isMobile
                ? "text-[2rem] leading-[1.1]"
                : "text-[32px] leading-[1.1]",
            )}
          >
            Let&apos;s get to work, how can I help?
          </h2>
          <p
            className={cn(
              "text-muted-foreground",
              isMobile ? "text-sm leading-5" : "text-base leading-6",
            )}
          >
            Start with a task or jump into a saved session
          </p>
        </div>

        <div
          className="flex w-full flex-wrap items-center justify-center gap-2"
          aria-live="polite"
          aria-label="Suggestion actions"
        >
          {suggestions.map((suggestion, index) => (
            <SuggestionChip
              key={suggestion.label}
              suggestion={suggestion}
              index={index}
              prefersReduced={!!prefersReduced}
              onClick={onSuggestionClick}
            />
          ))}
        </div>
      </motion.div>
    </ConversationEmptyState>
  );
}

interface SuggestionChipProps {
  suggestion: (typeof suggestions)[number];
  index: number;
  prefersReduced: boolean;
  onClick: (text: string) => void;
}

function SuggestionChip({
  suggestion,
  index,
  prefersReduced,
  onClick,
}: SuggestionChipProps) {
  const handleClick = useCallback(() => {
    onClick(suggestion.prompt);
  }, [onClick, suggestion.prompt]);

  return (
    <motion.button
      type="button"
      initial={{ opacity: 0, y: prefersReduced ? 0 : 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        prefersReduced
          ? { duration: 0.01 }
          : { delay: 0.08 + index * 0.04, duration: 0.22, ease: "easeOut" }
      }
      onClick={handleClick}
      className={cn(
        "inline-flex items-center gap-2 rounded-full border border-border/60 bg-transparent px-4 py-3 text-[13px] leading-[18px] text-foreground/90 transition-[border-color,background-color,color] duration-150",
        "hover:border-border hover:bg-foreground/[0.04] hover:text-foreground",
      )}
    >
      {suggestion.Icon ? (
        <suggestion.Icon
          className={cn("size-4 shrink-0", suggestion.accentClassName)}
        />
      ) : null}
      <span>{suggestion.label}</span>
    </motion.button>
  );
}
