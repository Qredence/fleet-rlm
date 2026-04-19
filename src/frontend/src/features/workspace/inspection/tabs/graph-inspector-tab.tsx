import { useEffect, useState, memo } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { GitBranch, Maximize2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Streamdown } from "@/components/ui/streamdown";
import { ArtifactGraph } from "@/features/workspace/inspection/artifact-graph";
import { summarizeArtifactStep } from "@/features/workspace/inspection/parsers/artifact-payload-summaries";
import type { ExecutionStep } from "@/features/workspace/use-workspace";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import { DetailBlock, stringifyValue } from "../inspector-ui";
import { InspectorTabPanel } from "../inspector-tab-panel";
import {
  MorphingDialog,
  MorphingDialogClose,
  MorphingDialogContainer,
  MorphingDialogContent,
  MorphingDialogTrigger,
} from "@/components/ui/morphing-dialog";

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
    <InspectorTabPanel value="graph">
      <div className={inspectorStyles.graph.statsGrid}>
        <Card className={inspectorStyles.card.root}>
          <CardHeader className={inspectorStyles.card.header}>
            <CardDescription>Steps</CardDescription>
            <CardTitle className="text-xl font-semibold text-foreground">{steps.length}</CardTitle>
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
        <CardContent className="p-0">
          <div className={cn(inspectorStyles.graph.canvas, "relative")}>
            <ArtifactGraph
              steps={steps}
              activeStepId={activeStepId}
              onSelectStep={(stepId) => setActiveStepId(stepId)}
              isVisible
            />

            <MorphingDialog transition={{ type: "spring", bounce: 0, duration: 0.35 }}>
              <MorphingDialogTrigger
                className="absolute top-2 left-2 z-10 flex items-center gap-1.5 rounded-full border border-border-subtle/80 bg-background/80 px-3 py-1.5 text-xs font-medium text-muted-foreground backdrop-blur-sm transition-colors hover:border-border hover:text-foreground"
                aria-label="Expand graph"
              >
                <Maximize2 className="size-3" />
                Expand
              </MorphingDialogTrigger>

              <MorphingDialogContainer>
                <MorphingDialogContent className="relative flex h-modal w-[80vw] max-w-5xl flex-col overflow-hidden rounded-2xl border border-border-subtle/80 bg-card shadow-2xl">
                  <ArtifactGraph
                    steps={steps}
                    activeStepId={activeStepId}
                    onSelectStep={(stepId) => setActiveStepId(stepId)}
                    isVisible
                  />
                  <MorphingDialogClose
                    className="absolute top-3 right-3 z-10 flex size-7 items-center justify-center rounded-lg border border-border-subtle/80 bg-background/80 text-muted-foreground backdrop-blur-sm transition-colors hover:border-border hover:text-foreground"
                    variants={{
                      initial: { opacity: 0, scale: 0.8 },
                      animate: { opacity: 1, scale: 1 },
                      exit: { opacity: 0, scale: 0.8 },
                    }}
                  >
                    <X className="size-3.5" />
                  </MorphingDialogClose>
                </MorphingDialogContent>
              </MorphingDialogContainer>
            </MorphingDialog>
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
            <Streamdown
              content={summarizeArtifactStep(selectedStep)}
              streaming={false}
              className="text-sm text-foreground"
            />
            <div className={inspectorStyles.badge.row}>
              <Badge variant="secondary" className={cn(inspectorStyles.badge.meta, "capitalize")}>
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
    </InspectorTabPanel>
  );
});
