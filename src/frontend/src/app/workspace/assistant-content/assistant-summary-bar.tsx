import { cn } from "@/lib/utils";
import type { InspectorTab } from "@/features/workspace/use-workspace";
import type { AssistantContentModel } from "@/app/workspace/assistant-content/model";

type SummaryPill = {
  key: string;
  label: string;
  tab: InspectorTab;
};

function summaryPills(summary: AssistantContentModel["summary"]) {
  const pills: SummaryPill[] = [];
  if (summary.trajectoryCount > 0) {
    pills.push({
      key: "trajectory",
      label: `${summary.trajectoryCount} trajector${summary.trajectoryCount === 1 ? "y" : "ies"}`,
      tab: "trajectory",
    });
  }
  if (summary.toolSessionCount > 0) {
    pills.push({
      key: "execution-sessions",
      label: `${summary.toolSessionCount} tool session${summary.toolSessionCount === 1 ? "" : "s"}`,
      tab: "execution",
    });
  }
  if (summary.sourceCount > 0) {
    pills.push({
      key: "evidence-sources",
      label: `${summary.sourceCount} source${summary.sourceCount === 1 ? "" : "s"}`,
      tab: "evidence",
    });
  }
  if (summary.attachmentCount > 0) {
    pills.push({
      key: "evidence-attachments",
      label: `${summary.attachmentCount} attachment${summary.attachmentCount === 1 ? "" : "s"}`,
      tab: "evidence",
    });
  }
  if (summary.sandboxActive) {
    pills.push({
      key: "execution-sandbox",
      label: "sandbox active",
      tab: "execution",
    });
  }
  summary.runtimeBadges.forEach((badge, index) => {
    pills.push({
      key: `runtime-${index}-${badge}`,
      label: badge,
      tab: "execution",
    });
  });
  return pills;
}

export function AssistantSummaryBar({
  summary,
  onOpenTab,
}: {
  summary: AssistantContentModel["summary"];
  onOpenTab?: (tab: InspectorTab) => void;
}) {
  if (!summary.show) return null;

  const pills = summaryPills(summary);
  if (pills.length === 0) return null;

  return (
    <div
      className="flex flex-wrap gap-2"
      data-slot="assistant-summary-bar"
      aria-label="Assistant summary"
    >
      {pills.map((pill) => (
        <button
          key={pill.key}
          type="button"
          className={cn(
            "rounded-full border border-border-subtle/80 bg-muted/20 px-2.5 py-1 text-[11px] leading-none text-muted-foreground transition-colors hover:border-border-strong hover:bg-muted/28",
          )}
          onClick={() => onOpenTab?.(pill.tab)}
        >
          {pill.label}
        </button>
      ))}
    </div>
  );
}
