import { Code2, FileSearch, GitBranch, Lightbulb, type LucideIcon } from "lucide-react";

import type { SuggestionItem } from "@/components/agent-elements/input/suggestions";
import { cn } from "@/lib/utils";

export type WorkspaceChatSuggestion = {
  label: string;
  prompt: string;
  description?: string;
  Icon?: LucideIcon;
  accentClassName?: string;
};

/** Suggestions aligned with the Daytona-backed execution runtime. */
export const WORKSPACE_CHAT_SUGGESTIONS: readonly WorkspaceChatSuggestion[] = [
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

export function workspaceChatSuggestionItems(): SuggestionItem[] {
  return WORKSPACE_CHAT_SUGGESTIONS.map((suggestion, index) => ({
    id: `${suggestion.label}-${index}`,
    label: suggestion.label,
    value: suggestion.prompt,
    icon: suggestion.Icon ? (
      <suggestion.Icon className={cn("size-4", suggestion.accentClassName)} />
    ) : undefined,
    className: suggestion.description ? "items-start" : undefined,
  }));
}
