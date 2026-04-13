import { memo } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Streamdown } from "@/components/ui/streamdown";
import { cn } from "@/lib/utils";
import type { AssistantContentModel } from "@/features/workspace/ui/assistant-content/model";
import {
  inspectorStyles,
  inspectorInsetClass,
} from "@/features/workspace/ui/inspector/inspector-styles";
import { renderBadges, statusTone } from "../ui/inspector-ui";
import { InspectorTabPanel } from "../inspector-tab-panel";

export const TrajectoryInspectorTab = memo(function TrajectoryInspectorTab({
  model,
}: {
  model: AssistantContentModel;
}) {
  const items = model.trajectory.items;
  return (
    <InspectorTabPanel value="trajectory">
      {model.trajectory.overview ? (
        <Card className={inspectorStyles.card.root}>
          <CardHeader className={inspectorStyles.card.header}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <CardTitle className="text-sm font-medium">Planning</CardTitle>
                <CardDescription>Overview of the reasoning path for this turn.</CardDescription>
              </div>
              {model.trajectory.overview.duration != null ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {Math.round(model.trajectory.overview.duration)}s
                </Badge>
              ) : null}
            </div>
            {renderBadges(model.trajectory.overview.runtimeBadges)}
          </CardHeader>
          <CardContent className={inspectorStyles.card.content}>
            <div className="text-sm leading-6 text-foreground">
              <Streamdown content={model.trajectory.overview.text} streaming={false} />
            </div>
          </CardContent>
        </Card>
      ) : null}

      {items.length === 0 ? (
        <Card className={inspectorStyles.card.root}>
          <CardHeader className={inspectorStyles.card.header}>
            <CardTitle className="text-sm font-medium">No trajectory recorded</CardTitle>
            <CardDescription>
              This turn does not include structured reasoning steps.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        items.map((item) => {
          const tone = statusTone(item.status);
          return (
            <Card
              key={item.id}
              className={cn(
                inspectorStyles.card.root,
                item.status === "failed" && "border-destructive/30 bg-destructive/5",
              )}
            >
              <CardHeader className={inspectorStyles.card.header}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <CardTitle className="text-sm font-medium">{item.title}</CardTitle>
                    {item.body ? (
                      <CardDescription>
                        Full reasoning for this trajectory step.
                      </CardDescription>
                    ) : null}
                  </div>
                  <Badge variant={tone.variant} className={inspectorStyles.badge.status}>
                    {tone.label}
                  </Badge>
                </div>
                {renderBadges(item.runtimeBadges)}
              </CardHeader>
              {item.body || item.details?.length ? (
                <CardContent className={inspectorStyles.card.contentStack}>
                  {item.body ? (
                    <div className="text-sm leading-6 text-foreground">
                      <Streamdown content={item.body} streaming={false} />
                    </div>
                  ) : null}
                  {!item.body && item.details?.length ? (
                    <div className={inspectorStyles.stack.compact}>
                      {item.details.map((detail, index) => (
                        <div
                          key={`${item.id}-detail-${index}`}
                          className={cn(inspectorInsetClass(), "text-sm")}
                        >
                          {detail}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </CardContent>
              ) : null}
            </Card>
          );
        })
      )}
    </InspectorTabPanel>
  );
});
