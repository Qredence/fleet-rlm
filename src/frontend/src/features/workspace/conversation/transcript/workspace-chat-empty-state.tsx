import { Code2, FileSearch, GitBranch, Lightbulb, Terminal, type LucideIcon } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { StateNotice } from "@/components/product";
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
        className="flex w-full max-w-3xl flex-col items-center gap-4 pb-10"
      >
        <StateNotice
          icon={<Terminal className="size-10 text-muted-foreground/40" />}
          title="Start a conversation"
          description="Type a message below to begin working with the AI assistant"
          className="w-full py-0"
          titleClassName="text-5xl font-medium leading-tight tracking-tighter-custom"
        />

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
        size="default"
        className={cn(
          "group inline-flex h-auto flex-col items-start gap-0.5 rounded-xl border border-border bg-card/50 px-4 py-3 text-left whitespace-normal transition-all duration-200",
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
