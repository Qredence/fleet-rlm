import { Badge } from "@/components/ui/badge";
import type { InspectorTab } from "@/screens/workspace/use-workspace";
import type { AssistantContentModel } from "@/app/workspace/assistant-content/model";
import {
  inspectorStyles,
  inspectorPreviewButtonClass,
} from "@/app/workspace/inspector/inspector-styles";
import { statusTone } from "@/app/workspace/inspector/utils/inspector-utils";

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
      <div className={inspectorStyles.heading.section}>Execution</div>
      <div className={inspectorStyles.stack.compact}>
        {execution.highlights.map((highlight) => {
          const tone = statusTone(highlight.status);
          return (
            <button
              key={highlight.id}
              type="button"
              className={inspectorPreviewButtonClass()}
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
                    <Badge
                      variant="secondary"
                      className={inspectorStyles.badge.meta}
                    >
                      {highlight.count}x
                    </Badge>
                  ) : null}
                  <Badge
                    variant={tone.variant}
                    className={inspectorStyles.badge.status}
                  >
                    {tone.label}
                  </Badge>
                </div>
              </div>
              {highlight.runtimeBadges.length ? (
                <div className={inspectorStyles.badge.row}>
                  {highlight.runtimeBadges.slice(0, 3).map((badge) => (
                    <Badge
                      key={`${highlight.id}-${badge}`}
                      variant="secondary"
                      className={inspectorStyles.badge.meta}
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
