import { Code2, FileSearch, GitBranch, Lightbulb, type LucideIcon } from "lucide-react";

import type { SuggestionItem } from "@/components/agent-elements/input/suggestions";

export type WorkspaceChatSuggestion = {
  label: string;
  prompt: string;
  description?: string;
  Icon?: LucideIcon;
};

/** Suggestions aligned with the Daytona-backed execution runtime. */
export const WORKSPACE_CHAT_SUGGESTIONS: readonly WorkspaceChatSuggestion[] = [
  {
    label: "Build a feature",
    prompt: "Help me build a new feature for my project",
    description: "Plan, code, and test",
    Icon: Code2,
  },
  {
    label: "Debug an issue",
    prompt: "Help me debug this issue in my codebase",
    description: "Analyze and fix bugs",
    Icon: FileSearch,
  },
  {
    label: "Review changes",
    prompt: "Review my recent code changes and suggest improvements",
    description: "Code review & suggestions",
    Icon: GitBranch,
  },
  {
    label: "Explore ideas",
    prompt: "Help me explore different approaches to solve this problem",
    description: "Brainstorm solutions",
    Icon: Lightbulb,
  },
] as const;

export function workspaceChatSuggestionItems(): SuggestionItem[] {
  return WORKSPACE_CHAT_SUGGESTIONS.map((suggestion, index) => ({
    id: `${suggestion.label}-${index}`,
    label: suggestion.label,
    value: suggestion.prompt,
    icon: suggestion.Icon ? (
      <suggestion.Icon className="size-3.5" />
    ) : undefined,
  }));
}
