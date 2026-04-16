import { memo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { AssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import { renderBadges, statusTone } from "../inspector-ui";
import { InspectorTabPanel } from "../inspector-tab-panel";
import { TrajectoryInspectorContent } from "./trajectory-inspector-tab";

export const MessageInspectorTab = memo(function MessageInspectorTab({
  model,
  status,
}: {
  model: AssistantContentModel;
  status: "pending" | "running" | "completed" | "failed";
}) {
  const tone = statusTone(status);
  const summaryBadges = [
    ...model.summary.runtimeBadges,
    ...(model.summary.sandboxActive ? ["sandbox active"] : []),
  ];
  const hasSummaryMetrics =
    model.summary.trajectoryCount > 0 ||
    model.summary.toolSessionCount > 0 ||
    model.summary.sourceCount > 0 ||
    model.summary.attachmentCount > 0 ||
    summaryBadges.length > 0;

  return (
    <InspectorTabPanel value="message">
      <Card className={inspectorStyles.card.root}>
        <CardHeader className={inspectorStyles.card.header}>
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="text-sm font-medium text-foreground">Assistant turn</CardTitle>
            <Badge variant={tone.variant} className={inspectorStyles.badge.status}>
              {tone.label}
            </Badge>
          </div>
        </CardHeader>
        {hasSummaryMetrics ? (
          <CardContent className={inspectorStyles.card.content}>
            <div className={inspectorStyles.badge.row}>
              {model.summary.trajectoryCount > 0 ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {`${model.summary.trajectoryCount} ${model.summary.trajectoryCount === 1 ? "trajectory" : "trajectories"}`}
                </Badge>
              ) : null}
              {model.summary.toolSessionCount > 0 ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {model.summary.toolSessionCount} tool session
                  {model.summary.toolSessionCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
              {model.summary.sourceCount > 0 ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {model.summary.sourceCount} source
                  {model.summary.sourceCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
              {model.summary.attachmentCount > 0 ? (
                <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                  {model.summary.attachmentCount} attachment
                  {model.summary.attachmentCount === 1 ? "" : "s"}
                </Badge>
              ) : null}
              {summaryBadges.length > 0 ? renderBadges(summaryBadges, "secondary") : null}
            </div>
          </CardContent>
        ) : null}
      </Card>

      <TrajectoryInspectorContent model={model} showEmptyState={false} />
    </InspectorTabPanel>
  );
});
