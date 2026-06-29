import { memo, useEffect, useRef, useState } from "react";
import { AlertCircle, Code2, MessageSquare, Terminal, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

export type SandboxEventCategory = "code_exec" | "tool_call" | "output" | "error" | "status";

export interface SandboxActivityEvent {
  id: string;
  category: SandboxEventCategory;
  message: string;
  details?: Record<string, unknown>;
  timestamp: number;
}

const _CATEGORY_ICONS: Record<SandboxEventCategory, LucideIcon> = {
  code_exec: Code2,
  tool_call: Wrench,
  output: MessageSquare,
  error: AlertCircle,
  status: Terminal,
};

const _CATEGORY_LABELS: Record<SandboxEventCategory, string> = {
  code_exec: "Code",
  tool_call: "Tool",
  output: "Output",
  error: "Error",
  status: "Status",
};

const _CATEGORY_TONES: Record<SandboxEventCategory, string> = {
  code_exec: "text-info",
  tool_call: "text-warning",
  output: "text-muted-foreground",
  error: "text-destructive",
  status: "text-muted-foreground",
};

function CategoryIcon({ category }: { category: SandboxEventCategory }) {
  const Icon = _CATEGORY_ICONS[category] ?? Terminal;
  const tone = _CATEGORY_TONES[category] ?? "text-muted-foreground";
  return <Icon className={cn("size-3.5 shrink-0", tone)} />;
}

function formatEventTime(timestamp: number): string {
  const date = new Date(timestamp);
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

interface SandboxActivityPanelProps {
  events: SandboxActivityEvent[];
  isRunning?: boolean;
  className?: string;
  maxEvents?: number;
}

/**
 * SandboxActivityPanel — collapsible panel displaying categorized sandbox
 * events from the Daytona log stream.
 *
 * Shows events with category-specific icons and auto-collapses when the
 * sandbox finishes execution. Displays an event count badge when collapsed.
 */
export const SandboxActivityPanel = memo(function SandboxActivityPanel({
  events,
  isRunning = false,
  className,
  maxEvents = 200,
}: SandboxActivityPanelProps) {
  const [open, setOpen] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-collapse when execution finishes
  useEffect(() => {
    if (!isRunning && events.length > 0) {
      const timer = setTimeout(() => setOpen(false), 1500);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [isRunning, events.length]);

  // Auto-expand when new events arrive during execution
  useEffect(() => {
    if (isRunning) {
      setOpen(true);
    }
  }, [isRunning, events.length]);

  // Auto-scroll to bottom when new events arrive
  useEffect(() => {
    if (open && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events.length, open]);

  if (events.length === 0) {
    return null;
  }

  const visibleEvents = events.slice(-maxEvents);
  const errorCount = events.filter((e) => e.category === "error").length;
  const categoryCounts = visibleEvents.reduce<Record<string, number>>((acc, event) => {
    acc[event.category] = (acc[event.category] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className={cn(
        "not-prose w-full rounded-lg border border-border-subtle/80 shadow-sm",
        className,
      )}
    >
      <CollapsibleTrigger className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-muted/50 transition-colors">
        <div className="flex min-w-0 items-center gap-2">
          <Terminal className="size-4 shrink-0 text-muted-foreground" />
          <span className="font-medium text-foreground">Sandbox Activity</span>
          <Badge variant="secondary" className="gap-1 typo-caption">
            {visibleEvents.length} events
          </Badge>
          {errorCount > 0 ? (
            <Badge variant="destructive" className="gap-1 typo-caption">
              {errorCount} errors
            </Badge>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          {Object.entries(categoryCounts).map(([category, count]) => (
            <Badge
              key={category}
              variant="outline"
              className={cn(
                "gap-1 typo-caption",
                _CATEGORY_TONES[category as SandboxEventCategory] ?? "text-muted-foreground",
              )}
              title={_CATEGORY_LABELS[category as SandboxEventCategory] ?? category}
            >
              <CategoryIcon category={category as SandboxEventCategory} />
              <span>{count}</span>
            </Badge>
          ))}
        </div>
      </CollapsibleTrigger>
      <CollapsibleContent className="overflow-hidden data-[state=closed]:animate-out data-[state=open]:animate-in">
        <div ref={scrollRef} className="max-h-64 overflow-y-auto border-t border-border-subtle/50">
          <ul className="divide-y divide-border-subtle/30">
            {visibleEvents.map((event) => (
              <li
                key={event.id}
                className="flex items-start gap-2 px-3 py-1.5 text-xs hover:bg-muted/30"
              >
                <CategoryIcon category={event.category} />
                <span className="min-w-0 flex-1 text-muted-foreground">
                  <span className="font-mono typo-helper text-muted-foreground/60 mr-1.5">
                    {formatEventTime(event.timestamp)}
                  </span>
                  <span className="text-foreground/90">{event.message}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
});
