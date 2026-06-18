import { memo } from "react";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { AssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import {
  TrajectoryChain,
  TrajectoryChainStep,
} from "@/features/workspace/inspection/trajectory-chain";

export const TrajectoryInspectorContent = memo(function TrajectoryInspectorContent({
  model,
  showEmptyState = true,
}: {
  model: AssistantContentModel;
  showEmptyState?: boolean;
}) {
  const items = model.trajectory.items;

  if (!model.trajectory.overview && items.length === 0 && !showEmptyState) {
    return null;
  }

  return (
    <>
      {!model.trajectory.overview && items.length === 0 ? (
        showEmptyState ? (
          <Card className={inspectorStyles.card.root}>
            <CardHeader className={inspectorStyles.card.header}>
              <CardTitle className="text-sm font-medium">No trajectory recorded</CardTitle>
              <CardDescription>
                This turn does not include structured reasoning steps.
              </CardDescription>
            </CardHeader>
          </Card>
        ) : null
      ) : (
        <TrajectoryChain>
          {model.trajectory.overview ? (
            <TrajectoryChainStep
              title="Planning"
              description="Overview of the reasoning path for this turn."
              status={model.trajectory.overview.isStreaming ? "running" : "completed"}
              body={model.trajectory.overview.text}
              badges={[
                ...model.trajectory.overview.runtimeBadges,
                ...(model.trajectory.overview.duration != null
                  ? [`${Math.round(model.trajectory.overview.duration)}s`]
                  : []),
              ]}
              defaultOpen
              isLast={items.length === 0}
            />
          ) : null}
          {items.map((item, index) => (
            <TrajectoryChainStep
              key={item.id}
              title={item.title}
              description={
                item.body ? "Full reasoning for this trajectory step." : "Trajectory step"
              }
              status={item.status}
              body={item.body}
              details={item.details}
              badges={item.runtimeBadges}
              defaultOpen={index === 0 && !model.trajectory.overview}
              isLast={index === items.length - 1}
              error={item.status === "failed"}
            />
          ))}
        </TrajectoryChain>
      )}
    </>
  );
});
