import { Badge } from "@/components/ui/badge";
import type { InspectorTab } from "@/lib/data/types";
import type {
  AssistantContentModel,
  ExecutionHighlight,
} from "@/features/rlm-workspace/assistant-content/types";
import { cn } from "@/lib/utils/cn";

function statusTone(
  status: ExecutionHighlight["status"],
): {
  label: string;
  variant: "secondary" | "warning" | "success" | "destructive-subtle";
} {
  switch (status) {
    case "pending":
      return { label: "Pending", variant: "secondary" };
    case "running":
      return { label: "Running", variant: "warning" };
    case "failed":
      return { label: "Failed", variant: "destructive-subtle" };
    default:
      return { label: "Completed", variant: "success" };
  }
}

function highlightButtonClasses() {
  return cn(
    "w-full rounded-2xl border border-border-subtle/80 bg-muted/18 px-3.5 py-3 text-left transition-colors hover:border-border-strong hover:bg-muted/28",
  );
}

export function ExecutionHighlightsGroup({
  execution,
  onOpenTab,
}: {
  execution: AssistantContentModel["execution"];
  onOpenTab?: (tab: InspectorTab) => void;
}) {
  if (!execution.hasChatHighlights) return null;

  return (
    <section className="space-y-2.5" data-slot="assistant-execution-highlights">
      <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        Execution
      </div>
      <div className="space-y-2">
        {execution.highlights.map((highlight) => {
          const tone = statusTone(highlight.status);
          return (
            <button
              key={highlight.id}
              type="button"
              className={highlightButtonClasses()}
              onClick={() => onOpenTab?.("execution")}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="space-y-1">
                  <div className="text-sm font-medium leading-5 text-foreground">
                    {highlight.label}
                  </div>
                  <div className="text-sm leading-5 text-muted-foreground">
                    {highlight.summary}
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  {highlight.count && highlight.count > 1 ? (
                    <Badge variant="outline" className="rounded-full text-[10px]">
                      {highlight.count}x
                    </Badge>
                  ) : null}
                  <Badge variant={tone.variant} className="rounded-full">
                    {tone.label}
                  </Badge>
                </div>
              </div>
              {highlight.runtimeBadges.length ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {highlight.runtimeBadges.slice(0, 3).map((badge) => (
                    <Badge
                      key={`${highlight.id}-${badge}`}
                      variant="outline"
                      className="rounded-full text-[10px]"
                    >
                      {badge}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}
