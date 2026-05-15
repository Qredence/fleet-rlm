import { Code2, FileSearch, GitBranch, Lightbulb, Terminal, type LucideIcon } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";

import { Suggestions } from "@/components/agent-elements/input/suggestions";
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
    <div
      className={cn(
        "flex h-full w-full items-center justify-center gap-0 px-0 pb-2 text-center",
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

        <div aria-live="polite" aria-label="Quick start suggestions" className="w-full">
          <Suggestions
            items={suggestions.map((suggestion, index) => ({
              id: `${suggestion.label}-${index}`,
              label: suggestion.label,
              value: suggestion.prompt,
              icon: suggestion.Icon ? (
                <suggestion.Icon className={cn("size-4", suggestion.accentClassName)} />
              ) : undefined,
              className: suggestion.description ? "items-start" : undefined,
            }))}
            onSelect={(item) => onSuggestionClick(item.value ?? item.label)}
            className="w-full justify-center"
            itemClassName="h-auto rounded-xl border border-border bg-card/50 px-4 py-3 text-left whitespace-normal hover:bg-card"
          />
        </div>
      </motion.div>
    </div>
  );
}
