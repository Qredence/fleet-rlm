import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { IterationSummary } from "@/features/workspace/use-workspace";

export function humanizeKind(kind: string): string {
  return kind
    .replace(/_/g, " ")
    .replace(/\brlm\b/gi, "RLM")
    .replace(/\brepl\b/gi, "REPL");
}

export function statusBadgeVariant(status: string): "default" | "secondary" | "outline" | "destructive" {
  if (status === "completed") return "default";
  if (status === "needs_human_review") return "outline";
  if (status === "error") return "destructive";
  if (status === "running") return "secondary";
  return "outline";
}

export function humanizeStatus(status: string): string {
  return status.replace(/_/g, " ");
}

export function IterationRow({
  iteration,
  selected,
  onSelect,
}: {
  iteration: IterationSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-xl border px-3 py-3 text-left transition-colors",
        selected
          ? "border-accent bg-accent/10"
          : "border-border-subtle/80 bg-muted/15 hover:bg-muted/30",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">iter {iteration.iteration}</Badge>
        <Badge variant={statusBadgeVariant(iteration.status)}>{iteration.status}</Badge>
        {iteration.phase ? <Badge variant="secondary">{iteration.phase}</Badge> : null}
      </div>
      <p className="mt-2 text-sm text-foreground">{iteration.summary}</p>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
        {iteration.durationMs != null ? <span>{iteration.durationMs}ms</span> : null}
        {iteration.callbackCount != null ? <span>{iteration.callbackCount} callbacks</span> : null}
        {iteration.finalized ? <span>finalized</span> : null}
      </div>
    </button>
  );
}
