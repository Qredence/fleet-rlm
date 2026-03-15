import { Clock } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { ConversationEmptyState } from "@/components/prompt-kit/conversation";
import { Suggestion } from "@/components/prompt-kit/suggestion";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils/cn";
import {
  SuggestionIconBolt,
  SuggestionIconSparkle,
  SuggestionIconTune,
} from "@/features/rlm-workspace/components/SuggestionIcons";
import {
  DISPLAY_SUBTITLE_STYLE,
  DISPLAY_TITLE_STYLE,
} from "@/features/rlm-workspace/chat-shell/chatMessageStyles";

const suggestions = [
  {
    text: "Analyze a codebase and extract its architecture",
    title: "Architecture pass",
    Icon: SuggestionIconBolt,
  },
  {
    text: "Summarize this document and find key insights",
    title: "Document brief",
    Icon: SuggestionIconTune,
  },
  {
    text: "Write and execute a Python script for me",
    title: "Python runner",
    Icon: SuggestionIconSparkle,
  },
  {
    text: "Review this work and surface what to improve",
    title: "Critique my work",
    Icon: SuggestionIconBolt,
  },
];

interface WorkspaceChatEmptyStateProps {
  isMobile: boolean;
  onSuggestionClick: (text: string) => void;
  showHistory?: boolean;
  onToggleHistory?: () => void;
  hasHistory?: boolean;
}

export function WorkspaceChatEmptyState({
  isMobile,
  onSuggestionClick,
  showHistory,
  onToggleHistory,
  hasHistory,
}: WorkspaceChatEmptyStateProps) {
  const prefersReduced = useReducedMotion();

  return (
    <ConversationEmptyState
      icon={null}
      className={cn(
        "h-auto w-full items-start justify-start gap-5 px-0 pb-2 pt-10 text-left",
        isMobile ? "pt-6" : "pt-8",
      )}
    >
      <div className="mb-4 flex w-full flex-col justify-center gap-2 pb-1.25">
        <Badge
          variant="outline"
          className="w-fit border-border-subtle/80 bg-card/60 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground"
        >
          Operator workspace
        </Badge>

        <div className="space-y-1">
          <h2 className="w-full text-foreground" style={DISPLAY_TITLE_STYLE}>
            Agentic Fleet Session
          </h2>
          <p className="w-full text-muted-foreground" style={DISPLAY_SUBTITLE_STYLE}>
            What do you need?
          </p>
        </div>
      </div>

      <div
        className="grid w-full grid-cols-2 gap-2"
        aria-live="polite"
        aria-label="Suggestion actions"
      >
        {suggestions.map((suggestion, index) => (
          <motion.div
            key={suggestion.text}
            initial={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={prefersReduced ? { duration: 0.01 } : { delay: 0.15 + index * 0.08 }}
          >
            <Suggestion
              suggestion={suggestion.text}
              onClick={onSuggestionClick}
              variant="outline"
              className="w-full justify-start gap-2 rounded-xl px-3"
            >
              <suggestion.Icon data-icon="inline-start" />
              {suggestion.title}
            </Suggestion>
          </motion.div>
        ))}
      </div>

      {hasHistory && !showHistory ? (
        <motion.div
          className="mt-4 w-full"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={prefersReduced ? { duration: 0.01 } : { delay: 0.35 }}
        >
          <button
            type="button"
            className="flex items-center gap-2 rounded-button border-subtle px-4 py-2.5 transition-colors hover:border-border-strong hover:bg-secondary/60"
            onClick={onToggleHistory}
          >
            <Clock className="size-4 text-muted-foreground" aria-hidden="true" />
            <span className="text-muted-foreground typo-label">View recent conversations</span>
          </button>
        </motion.div>
      ) : null}
    </ConversationEmptyState>
  );
}
