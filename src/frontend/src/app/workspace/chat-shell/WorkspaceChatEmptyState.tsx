import {
  BookOpen,
  Code2,
  GitBranch,
  Layers,
  MessageSquareText,
  Microscope,
  Zap,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { QredenceLogo } from "@/components/brand-mark";
import { cn } from "@/lib/utils";
import { useCallback } from "react";

const suggestions = [
  {
    text: "Analyze a codebase and extract its architecture",
    title: "Architecture pass",
    description: "Map modules, dependencies, and design patterns across a repo",
    Icon: GitBranch,
    accent: "text-blue-500",
    bg: "bg-blue-500/8 dark:bg-blue-500/12",
    border: "border-blue-500/20",
  },
  {
    text: "Summarize this document and find key insights",
    title: "Document brief",
    description: "Extract the most important points from any long document",
    Icon: BookOpen,
    accent: "text-violet-500",
    bg: "bg-violet-500/8 dark:bg-violet-500/12",
    border: "border-violet-500/20",
  },
  {
    text: "Write and execute a Python script for me",
    title: "Python runner",
    description: "Generate, run, and iterate on Python code in a live sandbox",
    Icon: Code2,
    accent: "text-emerald-500",
    bg: "bg-emerald-500/8 dark:bg-emerald-500/12",
    border: "border-emerald-500/20",
  },
  {
    text: "Review this work and surface what to improve",
    title: "Critique my work",
    description: "Get structured feedback on code, writing, or any artifact",
    Icon: Microscope,
    accent: "text-amber-500",
    bg: "bg-amber-500/8 dark:bg-amber-500/12",
    border: "border-amber-500/20",
  },
  {
    text: "Break this task into steps and execute them",
    title: "Plan & execute",
    description: "Decompose complex goals into tracked, runnable sub-tasks",
    Icon: Layers,
    accent: "text-rose-500",
    bg: "bg-rose-500/8 dark:bg-rose-500/12",
    border: "border-rose-500/20",
  },
  {
    text: "Ask me anything about this codebase",
    title: "Codebase Q&A",
    description: "Query your code with natural language for instant answers",
    Icon: MessageSquareText,
    accent: "text-cyan-500",
    bg: "bg-cyan-500/8 dark:bg-cyan-500/12",
    border: "border-cyan-500/20",
  },
];

interface WorkspaceChatEmptyStateProps {
  isMobile: boolean;
  onSuggestionClick: (text: string) => void;
}

export function WorkspaceChatEmptyState({
  isMobile,
  onSuggestionClick,
}: WorkspaceChatEmptyStateProps) {
  const prefersReduced = useReducedMotion();

  const visibleSuggestions = isMobile ? suggestions.slice(0, 4) : suggestions;

  return (
    <ConversationEmptyState
      icon={null}
      className={cn(
        "h-auto w-full items-center justify-start gap-0 px-0 pb-2 text-center",
        isMobile ? "pt-8" : "pt-10",
      )}
    >
      {/* Brand logo mark */}
      <motion.div
        initial={{ opacity: 0, scale: prefersReduced ? 1 : 0.85 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={
          prefersReduced
            ? { duration: 0.01 }
            : { duration: 0.4, ease: "easeOut" }
        }
        className="mb-6 flex items-center justify-center"
      >
        <div className="relative flex size-14 items-center justify-center rounded-2xl border border-border/60 bg-background shadow-sm ring-1 ring-border/30">
          <QredenceLogo
            className={cn("size-7 text-foreground", isMobile && "size-6")}
          />
          <span className="absolute -bottom-1 -right-1 flex size-4 items-center justify-center rounded-full bg-primary shadow-sm">
            <Zap className="size-2.5 text-primary-foreground" />
          </span>
        </div>
      </motion.div>

      {/* Headline */}
      <motion.div
        initial={{ opacity: 0, y: prefersReduced ? 0 : 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={
          prefersReduced ? { duration: 0.01 } : { delay: 0.1, duration: 0.35 }
        }
        className="mb-2 flex flex-col items-center gap-1.5"
      >
        <h2
          className="text-foreground"
          style={{
            fontSize: isMobile ? "1.35rem" : "1.6rem",
            fontWeight: 600,
            lineHeight: 1.2,
            letterSpacing: "-0.02em",
          }}
        >
          What can I help you build?
        </h2>
        <p
          className="max-w-sm text-muted-foreground"
          style={{ fontSize: "0.9rem", lineHeight: 1.5, opacity: 0.75 }}
        >
          Agentic Fleet is ready — pick a starting point or describe your task
          below.
        </p>
      </motion.div>

      {/* Suggestion cards */}
      <div
        className={cn(
          "mt-6 grid w-full gap-2.5",
          isMobile ? "grid-cols-1" : "grid-cols-2",
        )}
        aria-live="polite"
        aria-label="Suggestion actions"
      >
        {visibleSuggestions.map((suggestion, index) => (
          <SuggestionCard
            key={suggestion.text}
            suggestion={suggestion}
            index={index}
            prefersReduced={!!prefersReduced}
            onClick={onSuggestionClick}
          />
        ))}
      </div>

      <motion.p
        className="mt-5 max-w-md text-balance text-sm leading-6 text-muted-foreground"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={prefersReduced ? { duration: 0.01 } : { delay: 0.55 }}
      >
        {isMobile
          ? "Open the sidebar anytime to jump between recent sessions, workbench tools, and volumes."
          : "Use the left rail to jump between recent sessions, workbench tools, and volumes."}
      </motion.p>
    </ConversationEmptyState>
  );
}

interface SuggestionCardProps {
  suggestion: (typeof suggestions)[number];
  index: number;
  prefersReduced: boolean;
  onClick: (text: string) => void;
}

function SuggestionCard({
  suggestion,
  index,
  prefersReduced,
  onClick,
}: SuggestionCardProps) {
  const handleClick = useCallback(() => {
    onClick(suggestion.text);
  }, [onClick, suggestion.text]);

  return (
    <motion.button
      type="button"
      key={suggestion.text}
      initial={{ opacity: 0, y: prefersReduced ? 0 : 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        prefersReduced
          ? { duration: 0.01 }
          : { delay: 0.2 + index * 0.06, duration: 0.3 }
      }
      onClick={handleClick}
      className={cn(
        "group flex w-full cursor-pointer items-start gap-3 rounded-xl border p-3.5 text-left",
        "bg-background transition-all duration-150",
        "hover:shadow-sm hover:border-border",
        suggestion.border,
      )}
    >
      <div
        className={cn(
          "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg",
          suggestion.bg,
        )}
      >
        <suggestion.Icon className={cn("size-4", suggestion.accent)} />
      </div>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-sm font-medium text-foreground leading-tight">
          {suggestion.title}
        </span>
        <span className="text-xs text-muted-foreground leading-snug">
          {suggestion.description}
        </span>
      </div>
    </motion.button>
  );
}
