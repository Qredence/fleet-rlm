import { useEffect, useState, memo } from "react";
import { TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { GitBranch } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { ArtifactGraph } from "@/features/artifacts/ArtifactGraph";
import { summarizeArtifactStep } from "@/features/artifacts/parsers/artifactPayloadSummaries";
import type { ExecutionStep } from "@/lib/data/artifactTypes";
import { inspectorStyles } from "@/features/rlm-workspace/shared/inspector-styles";
import { DetailBlock, stringifyValue } from "../ui/inspector-ui";

export const GraphInspectorTab = memo(function GraphInspectorTab({
  steps,
}: {
  steps: ExecutionStep[];
}) {
  const [activeStepId, setActiveStepId] = useState<string | undefined>(steps[steps.length - 1]?.id);

  useEffect(() => {
    setActiveStepId((current) => {
      if (current && steps.some((step) => step.id === current)) {
        return current;
      }
      return steps[steps.length - 1]?.id;
    });
  }, [steps]);

  const selectedStep = steps.find((step) => step.id === activeStepId) ?? steps[steps.length - 1];
  const laneCount = new Set(
    steps
      .map((step) => step.lane_key ?? `${step.actor_kind ?? "unknown"}:${step.actor_id ?? ""}`)
      .filter(Boolean),
  ).size;
  const branchingParents = new Set(
    steps
      .map((step) => step.parent_id)
      .filter((parentId, index, all) => parentId && all.indexOf(parentId) !== index),
  ).size;

  return (
    <TabsContent value="graph" className="min-h-0 flex-1">
      <ScrollArea className="h-full">
        <div className={inspectorStyles.tab.content}>
          <div className={inspectorStyles.graph.statsGrid}>
            <Card className={inspectorStyles.card.root}>
              <CardHeader className={inspectorStyles.card.header}>
                <CardDescription>Steps</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">
                  {steps.length}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card className={inspectorStyles.card.root}>
              <CardHeader className={inspectorStyles.card.header}>
                <CardDescription>Execution lanes</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">{laneCount}</CardTitle>
              </CardHeader>
            </Card>
            <Card className={inspectorStyles.card.root}>
              <CardHeader className={inspectorStyles.card.header}>
                <CardDescription>Branch points</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">
                  {branchingParents}
                </CardTitle>
              </CardHeader>
            </Card>
          </div>

          <Card className={inspectorStyles.card.root}>
            <CardHeader className={inspectorStyles.card.header}>
              <div className="flex items-center gap-2">
                <GitBranch className="size-4 text-accent" />
                <CardTitle className="text-sm font-medium text-foreground">Relationships</CardTitle>
              </div>
              <CardDescription>
                Parent-child lineage, actor lanes, and delegated branches for this turn.
              </CardDescription>
            </CardHeader>
            <CardContent className={inspectorStyles.card.content}>
              <div className={inspectorStyles.graph.canvas}>
                <ArtifactGraph
                  steps={steps}
                  activeStepId={activeStepId}
                  onSelectStep={(stepId) => setActiveStepId(stepId)}
                  isVisible
                />
              </div>
            </CardContent>
          </Card>

          {selectedStep ? (
            <Card className={inspectorStyles.card.root}>
              <CardHeader className={inspectorStyles.card.header}>
                <CardTitle className="text-sm font-medium text-foreground">Selected node</CardTitle>
                <CardDescription>{selectedStep.label}</CardDescription>
              </CardHeader>
              <CardContent className={inspectorStyles.card.contentStack}>
                <div className="text-sm text-foreground">{summarizeArtifactStep(selectedStep)}</div>
                <div className={inspectorStyles.badge.row}>
                  <Badge
                    variant="secondary"
                    className={cn(inspectorStyles.badge.meta, "capitalize")}
                  >
                    {selectedStep.type}
                  </Badge>
                  {selectedStep.actor_kind ? (
                    <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                      {selectedStep.actor_kind.replace(/_/g, " ")}
                    </Badge>
                  ) : null}
                  {selectedStep.actor_id ? (
                    <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                      {selectedStep.actor_id}
                    </Badge>
                  ) : null}
                </div>
                <DetailBlock label="Input" value={stringifyValue(selectedStep.input)} />
                <DetailBlock label="Output" value={stringifyValue(selectedStep.output)} />
              </CardContent>
            </Card>
          ) : null}
        </div>
      </ScrollArea>
    </TabsContent>
  );
});
