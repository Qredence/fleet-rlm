import { Code2, FileSearch, GitBranch, Lightbulb, type LucideIcon } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { cn } from "@/lib/utils";

type WorkspaceSuggestion = {
  label: string;
  prompt: string;
  description?: string;
  Icon?: LucideIcon;
  accentClassName?: string;
};

/**
 * Suggestions aligned with the Daytona-backed execution runtime.
 * These prompt examples highlight coding, analysis, and repository tasks.
 */
const suggestions: readonly WorkspaceSuggestion[] = [
  {
    label: "Build a feature",
    prompt: "Help me build a new feature for my project",
    description: "Plan, code, and test",
    Icon: Code2,
    accentClassName: "text-emerald-500",
  },
  {
    label: "Debug an issue",
    prompt: "Help me debug this issue in my codebase",
    description: "Analyze and fix bugs",
    Icon: FileSearch,
    accentClassName: "text-amber-500",
  },
  {
    label: "Review changes",
    prompt: "Review my recent code changes and suggest improvements",
    description: "Code review & suggestions",
    Icon: GitBranch,
    accentClassName: "text-indigo-500",
  },
  {
    label: "Explore ideas",
    prompt: "Help me explore different approaches to solve this problem",
    description: "Brainstorm solutions",
    Icon: Lightbulb,
    accentClassName: "text-fuchsia-500",
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
        "h-full w-full items-center justify-center gap-0 px-0 pb-2 text-center",
        isMobile ? "pt-6" : "pt-0",
      )}
    >
      <motion.div
        initial={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={prefersReduced ? { duration: 0.01 } : { duration: 0.28, ease: "easeOut" }}
        className="flex w-full max-w-3xl flex-col items-center gap-4"
      >
        <div className="flex w-full flex-col items-center gap-1.5">
          <h2
            className={cn(
              "font-semibold tracking-[-0.02em] text-foreground",
              isMobile ? "text-2xl leading-tight" : "text-3xl leading-tight",
            )}
          >
            What would you like to build?
          </h2>
          <p
            className={cn(
              "text-muted-foreground max-w-md",
              isMobile ? "text-sm leading-relaxed" : "text-base leading-relaxed",
            )}
          >
            Describe a task and I&apos;ll help you plan, code, and execute it in a secure sandbox
          </p>
        </div>

        <Suggestions
          wrap
          className="w-full justify-center"
          aria-live="polite"
          aria-label="Quick start suggestions"
        >
          {suggestions.map((suggestion, index) => (
            <AnimatedSuggestion
              key={`${suggestion.label}-${index}`}
              suggestion={suggestion}
              index={index}
              prefersReduced={!!prefersReduced}
              onClick={onSuggestionClick}
            />
          ))}
        </Suggestions>

        <p className="text-xs text-muted-foreground/70 mt-2">
          Sessions run in isolated Daytona sandboxes with persistent storage
        </p>
      </motion.div>
    </ConversationEmptyState>
  );
}

interface AnimatedSuggestionProps {
  suggestion: (typeof suggestions)[number];
  index: number;
  prefersReduced: boolean;
  onClick: (text: string) => void;
}

function AnimatedSuggestion({
  suggestion,
  index,
  prefersReduced,
  onClick,
}: AnimatedSuggestionProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: prefersReduced ? 0 : 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        prefersReduced
          ? { duration: 0.01 }
          : { delay: 0.1 + index * 0.05, duration: 0.25, ease: "easeOut" }
      }
    >
      <Suggestion
        suggestion={suggestion.prompt}
        onClick={onClick}
        className={cn(
          "group inline-flex flex-col items-start gap-0.5 rounded-xl border border-border/50 bg-card/50 px-4 py-3 text-left transition-all duration-200",
          "hover:border-border hover:bg-card hover:shadow-sm",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        )}
      >
        <span className="flex items-center gap-2">
          {suggestion.Icon ? (
            <suggestion.Icon
              className={cn("size-4 shrink-0 transition-colors", suggestion.accentClassName)}
            />
          ) : null}
          <span className="text-sm font-medium text-foreground">{suggestion.label}</span>
        </span>
        {suggestion.description && (
          <span className="text-xs text-muted-foreground pl-6">{suggestion.description}</span>
        )}
      </Suggestion>
    </motion.div>
  );
}
