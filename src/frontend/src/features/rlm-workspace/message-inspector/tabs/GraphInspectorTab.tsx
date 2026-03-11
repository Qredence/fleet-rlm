import { useEffect, useState, memo } from "react";
import { TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { GitBranch } from "lucide-react";
import { ArtifactGraph } from "@/components/domain/artifacts/ArtifactGraph";
import { summarizeArtifactStep } from "@/components/domain/artifacts/parsers/artifactPayloadSummaries";
import type { ExecutionStep } from "@/stores/artifactStore";
import { stringifyValue } from "../utils/inspector-utils";
import { DetailBlock } from "../components/inspector-components";

export const GraphInspectorTab = memo(function GraphInspectorTab({ steps }: { steps: ExecutionStep[] }) {
  const [activeStepId, setActiveStepId] = useState<string | undefined>(
    steps[steps.length - 1]?.id,
  );

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
        <div className="space-y-3 px-4 pb-4">
          <div className="grid gap-3 md:grid-cols-3">
            <Card className="gap-2 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardDescription>Steps</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">
                  {steps.length}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card className="gap-2 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardDescription>Execution lanes</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">
                  {laneCount}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card className="gap-2 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardDescription>Branch points</CardDescription>
                <CardTitle className="text-xl font-semibold text-foreground">
                  {branchingParents}
                </CardTitle>
              </CardHeader>
            </Card>
          </div>

          <Card className="gap-3 rounded-2xl border-border-subtle/80 shadow-none">
            <CardHeader className="px-4 pt-4">
              <div className="flex items-center gap-2">
                <GitBranch className="size-4 text-accent" />
                <CardTitle className="text-sm font-medium text-foreground">
                  Relationships
                </CardTitle>
              </div>
              <CardDescription>
                Parent-child lineage, actor lanes, and delegated branches for this turn.
              </CardDescription>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <div className="h-105 overflow-hidden rounded-2xl border border-border-subtle/80 bg-muted/15">
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
            <Card className="gap-3 rounded-2xl border-border-subtle/80 shadow-none">
              <CardHeader className="px-4 pt-4">
                <CardTitle className="text-sm font-medium text-foreground">
                  Selected node
                </CardTitle>
                <CardDescription>{selectedStep.label}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 px-4 pb-4">
                <div className="text-sm text-foreground">
                  {summarizeArtifactStep(selectedStep)}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="secondary" className="rounded-full capitalize">
                    {selectedStep.type}
                  </Badge>
                  {selectedStep.actor_kind ? (
                    <Badge variant="outline" className="rounded-full">
                      {selectedStep.actor_kind.replace(/_/g, " ")}
                    </Badge>
                  ) : null}
                  {selectedStep.actor_id ? (
                    <Badge variant="outline" className="rounded-full">
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
