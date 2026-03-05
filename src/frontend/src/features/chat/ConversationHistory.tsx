/**
 * ConversationHistory — displays saved chat conversations.
 *
 * Renders a scrollable list of past conversations with relative timestamps,
 * grouped by recency. Each item shows the conversation title (derived from
 * the first user message) and a delete button on hover/focus.
 *
 * Uses design-system tokens exclusively: `typo` for typography, CSS
 * variables for colors/spacing, `cn()` for conditional classes.
 */
import { useState, useCallback } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { MessageSquare, Trash2, Clock, X } from "lucide-react";
import { springs } from "@/lib/config/motion-config";
import { typo } from "@/lib/config/typo";
import type { Conversation } from "@/hooks/useChatHistory";
import { cn } from "@/components/ui/utils";
import { IconButton } from "@/components/ui/icon-button";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { ScrollArea } from "@/components/ui/scroll-area";

// ── Types ────────────────────────────────────────────────────────────

interface ConversationHistoryProps {
  conversations: Conversation[];
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onClearAll: () => void;
  onClose: () => void;
}

// ── Relative timestamp helper ────────────────────────────────────────

function relativeTime(isoDate: string): string {
  const now = Date.now();
  const then = new Date(isoDate).getTime();
  const diff = now - then;

  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;

  const weeks = Math.floor(days / 7);
  if (weeks < 4) return `${weeks}w ago`;

  return new Date(isoDate).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

// ── Grouping helper ──────────────────────────────────────────────────

type TimeGroup = "Today" | "Yesterday" | "This Week" | "Older";

function getTimeGroup(isoDate: string): TimeGroup {
  const now = new Date();
  const then = new Date(isoDate);
  const diffDays = Math.floor((now.getTime() - then.getTime()) / 86_400_000);

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return "This Week";
  return "Older";
}

function groupConversations(
  conversations: Conversation[],
): { group: TimeGroup; items: Conversation[] }[] {
  const order: TimeGroup[] = ["Today", "Yesterday", "This Week", "Older"];
  const map = new Map<TimeGroup, Conversation[]>();

  for (const conv of conversations) {
    const group = getTimeGroup(conv.updatedAt);
    if (!map.has(group)) map.set(group, []);
    map.get(group)!.push(conv);
  }

  return order
    .filter((g) => map.has(g))
    .map((g) => ({ group: g, items: map.get(g)! }));
}

// ── Phase badge helper ───────────────────────────────────────────────

function phaseBadgeLabel(phase: string): string | null {
  switch (phase) {
    case "understanding":
      return "Planning";
    case "generating":
      return "Generating";
    case "validating":
      return "Validating";
    case "complete":
      return "Complete";
    default:
      return null;
  }
}

// ── Component ────────────────────────────────────────────────────────

export function ConversationHistory({
  conversations,
  onSelect,
  onDelete,
  onClearAll,
  onClose,
}: ConversationHistoryProps) {
  const prefersReduced = useReducedMotion();
  const [confirmClear, setConfirmClear] = useState(false);

  const handleClear = useCallback(() => {
    if (confirmClear) {
      onClearAll();
      setConfirmClear(false);
    } else {
      setConfirmClear(true);
    }
  }, [confirmClear, onClearAll]);

  const groups = groupConversations(conversations);

  return (
    <motion.div
      initial={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
      transition={prefersReduced ? springs.instant : springs.default}
      className="flex flex-col w-full max-w-200 mx-auto"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-1 mb-4">
        <div className="flex items-center gap-2">
          <Clock className="size-4 text-muted-foreground" aria-hidden="true" />
          <span className="text-foreground" style={typo.h4}>
            Recent Conversations
          </span>
        </div>
        <div className="flex items-center gap-1">
          {conversations.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClear}
              className={cn(
                "transition-colors",
                confirmClear && "text-destructive",
              )}
            >
              {confirmClear ? "Confirm Clear" : "Clear All"}
            </Button>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <IconButton onClick={onClose} aria-label="Close history">
                  <X
                    className="size-4 text-muted-foreground"
                    aria-hidden="true"
                  />
                </IconButton>
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom">Close</TooltipContent>
          </Tooltip>
        </div>
      </div>

      {/* Conversation list */}
      {conversations.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12">
          <div className="size-10 rounded-lg bg-muted flex items-center justify-center mb-3">
            <MessageSquare
              className="size-5 text-muted-foreground"
              aria-hidden="true"
            />
          </div>
          <p className="text-muted-foreground" style={typo.label}>
            No conversations yet
          </p>
          <p className="text-muted-foreground mt-1" style={typo.caption}>
            Start a chat to see your history here
          </p>
        </div>
      ) : (
        <ScrollArea className="max-h-100">
          <div className="flex flex-col gap-5 pr-2">
            <AnimatePresence>
              {groups.map(({ group, items }) => (
                <div key={group} className="flex flex-col gap-1.5">
                  {/* Group label */}
                  <span
                    className="text-muted-foreground uppercase tracking-[0.12em] px-1 mb-0.5"
                    style={typo.micro}
                  >
                    {group}
                  </span>

                  {/* Items */}
                  {items.map((conv) => {
                    const badge = phaseBadgeLabel(conv.phase);
                    const msgCount = conv.messages.filter(
                      (m) => m.type === "user" || m.type === "assistant",
                    ).length;

                    return (
                      <motion.div
                        key={conv.id}
                        layout={!prefersReduced}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0, height: 0, marginBottom: 0 }}
                        transition={
                          prefersReduced ? springs.instant : springs.snappy
                        }
                        className="group flex items-center gap-3 w-full px-3 py-2.5 rounded-lg hover:bg-secondary/60 transition-colors border border-transparent hover:border-border-subtle"
                      >
                        <button
                          type="button"
                          className="flex flex-1 min-w-0 items-center gap-3 text-left rounded-[inherit] focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
                          onClick={() => onSelect(conv.id)}
                          aria-label={`Open conversation: ${conv.title}`}
                        >
                          {/* Icon */}
                          <div className="size-8 rounded-md bg-muted flex items-center justify-center shrink-0">
                            <MessageSquare
                              className="size-4 text-muted-foreground"
                              aria-hidden="true"
                            />
                          </div>

                          {/* Content */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span
                                className="text-foreground truncate"
                                style={typo.label}
                              >
                                {conv.title}
                              </span>
                              {badge && (
                                <span
                                  className={cn(
                                    "shrink-0 px-1.5 py-0.5 rounded-sm",
                                    conv.phase === "complete"
                                      ? "bg-accent/10 text-accent"
                                      : "bg-muted text-muted-foreground",
                                  )}
                                  style={typo.micro}
                                >
                                  {badge}
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 mt-0.5">
                              <span
                                className="text-muted-foreground"
                                style={typo.helper}
                              >
                                {msgCount} messages
                              </span>
                              <span
                                className="text-muted-foreground"
                                style={typo.helper}
                              >
                                &middot;
                              </span>
                              <span
                                className="text-muted-foreground"
                                style={typo.helper}
                              >
                                {relativeTime(conv.updatedAt)}
                              </span>
                            </div>
                          </div>
                        </button>

                        {/* Delete */}
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
                              <IconButton
                                aria-label={`Delete conversation: ${conv.title}`}
                                className="text-muted-foreground hover:text-destructive"
                                onClick={() => onDelete(conv.id)}
                              >
                                <Trash2
                                  className="size-3.5"
                                  aria-hidden="true"
                                />
                              </IconButton>
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="left">Delete</TooltipContent>
                        </Tooltip>
                      </motion.div>
                    );
                  })}
                </div>
              ))}
            </AnimatePresence>
          </div>
        </ScrollArea>
      )}
    </motion.div>
  );
}
