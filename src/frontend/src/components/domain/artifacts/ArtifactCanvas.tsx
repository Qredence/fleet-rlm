import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import { GitBranch, TerminalSquare, ListTree, FileCode2 } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useArtifactStore } from "@/stores/artifactStore";
import { ArtifactGraph } from "@/components/domain/artifacts/ArtifactGraph";
import { ArtifactREPL } from "@/components/domain/artifacts/ArtifactREPL";
import { ArtifactTimeline } from "@/components/domain/artifacts/ArtifactTimeline";
import { ArtifactPreview } from "@/components/domain/artifacts/ArtifactPreview";

export type ArtifactTab = "graph" | "repl" | "timeline" | "preview";

interface ArtifactCanvasProps {
  activeTab?: ArtifactTab;
  onTabChange?: (tab: ArtifactTab) => void;
  showTabs?: boolean;
}

function formatDuration(ms: number | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

export function ArtifactCanvas({
  activeTab: controlledActiveTab,
  onTabChange,
  showTabs = true,
}: ArtifactCanvasProps = {}) {
  const steps = useArtifactStore((state) => state.steps);
  const activeStepId = useArtifactStore((state) => state.activeStepId);
  const setActiveStepId = useArtifactStore((state) => state.setActiveStepId);
  const isControlled = controlledActiveTab !== undefined;
  const [internalActiveTab, setInternalActiveTab] =
    useState<ArtifactTab>("graph");
  const [hasUserSelectedTab, setHasUserSelectedTab] = useState(false);
  const [graphVisited, setGraphVisited] = useState(false);
  const activeTab = controlledActiveTab ?? internalActiveTab;

  const selectTab = useCallback(
    (tab: ArtifactTab, userInitiated = false) => {
      if (!isControlled) {
        setInternalActiveTab(tab);
        if (userInitiated) {
          setHasUserSelectedTab(true);
        }
      }
      onTabChange?.(tab);
    },
    [isControlled, onTabChange],
  );

  const summary = useMemo(() => {
    const first = steps[0];
    const last = steps[steps.length - 1];
    const typeCounts = steps.reduce(
      (acc, step) => {
        acc[step.type] += 1;
        return acc;
      },
      { llm: 0, repl: 0, tool: 0, memory: 0, output: 0 },
    );

    return {
      stepCount: steps.length,
      toolCount: typeCounts.tool,
      replCount: typeCounts.repl,
      outputCount: typeCounts.output,
      durationLabel: formatDuration(
        first && last ? last.timestamp - first.timestamp : undefined,
      ),
    };
  }, [steps]);

  const hasTimeline = summary.stepCount > 0;
  const hasReplContent = summary.replCount > 0 || summary.toolCount > 0;
  const hasPreviewContent = summary.outputCount > 0;

  useEffect(() => {
    if (isControlled || hasUserSelectedTab) return;
    selectTab(summary.stepCount > 0 ? "timeline" : "graph");
  }, [hasUserSelectedTab, isControlled, selectTab, summary.stepCount]);

  useEffect(() => {
    if (activeTab === "graph") {
      setGraphVisited(true);
    }
  }, [activeTab]);

  useEffect(() => {
    if (activeTab === "timeline" && !hasTimeline) {
      selectTab("graph");
    } else if (activeTab === "repl" && !hasReplContent) {
      selectTab(hasTimeline ? "timeline" : "graph");
    } else if (activeTab === "preview" && !hasPreviewContent) {
      selectTab(hasTimeline ? "timeline" : "graph");
    }
  }, [activeTab, hasPreviewContent, hasReplContent, hasTimeline, selectTab]);

  return (
    <motion.div
      className="flex h-full min-h-0 flex-col gap-2 overflow-hidden p-2 md:p-2.5"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <Tabs
        value={activeTab}
        onValueChange={(value) => {
          selectTab(value as ArtifactTab, true);
        }}
        className="flex h-full min-h-0 flex-col gap-2 overflow-hidden"
      >
        {showTabs ? (
          <TabsList
            className="h-8 max-w-full shrink-0 self-start overflow-x-auto rounded-md border border-border-subtle/70 bg-muted/45 p-0.5"
            style={{ height: "var(--touch-target-min-height)", minHeight: "var(--touch-target-min-height)" }}
          >
            <TabsTrigger
              value="graph"
              className="h-6 flex-none rounded-sm border border-transparent px-3 text-[11px] text-muted-foreground hover:bg-white/8 hover:text-foreground/95 data-[state=active]:border-white/20 data-[state=active]:bg-white/12 data-[state=active]:text-foreground"
            >
              <GitBranch className="size-3.5" />
              Graph
            </TabsTrigger>
            <TabsTrigger
              value="repl"
              disabled={!hasReplContent}
              className="h-6 flex-none rounded-sm border border-transparent px-3 text-[11px] text-muted-foreground hover:bg-white/8 hover:text-foreground/95 data-[state=active]:border-white/20 data-[state=active]:bg-white/12 data-[state=active]:text-foreground"
            >
              <TerminalSquare className="size-3.5" />
              REPL
            </TabsTrigger>
            <TabsTrigger
              value="timeline"
              disabled={!hasTimeline}
              className="h-6 flex-none rounded-sm border border-transparent px-3 text-[11px] text-muted-foreground hover:bg-white/8 hover:text-foreground/95 data-[state=active]:border-white/20 data-[state=active]:bg-white/12 data-[state=active]:text-foreground"
            >
              <ListTree className="size-3.5" />
              Timeline
            </TabsTrigger>
            <TabsTrigger
              value="preview"
              disabled={!hasPreviewContent}
              className="h-6 flex-none rounded-sm border border-transparent px-3 text-[11px] text-muted-foreground hover:bg-white/8 hover:text-foreground/95 data-[state=active]:border-white/20 data-[state=active]:bg-white/12 data-[state=active]:text-foreground"
            >
              <FileCode2 className="size-3.5" />
              Preview
            </TabsTrigger>
          </TabsList>
        ) : null}

        {graphVisited || activeTab === "graph" ? (
          <TabsContent
            value="graph"
            {...(graphVisited ? { forceMount: true as const } : {})}
            className="mt-0 flex-1 min-h-0 overflow-hidden"
            style={{ display: activeTab === "graph" ? "flex" : "none" }}
          >
            <ArtifactGraph
              steps={steps}
              activeStepId={activeStepId}
              onSelectStep={setActiveStepId}
              isVisible={activeTab === "graph"}
            />
          </TabsContent>
        ) : null}

        <TabsContent
          value="repl"
          forceMount
          className="mt-0 flex-1 min-h-0 overflow-hidden"
          style={{ display: activeTab === "repl" ? "flex" : "none" }}
        >
          <ArtifactREPL steps={steps} activeStepId={activeStepId} />
        </TabsContent>

        <TabsContent
          value="timeline"
          forceMount
          className="mt-0 flex-1 min-h-0 overflow-hidden"
          style={{ display: activeTab === "timeline" ? "flex" : "none" }}
        >
          <ArtifactTimeline
            steps={steps}
            activeStepId={activeStepId}
            onSelectStep={setActiveStepId}
          />
        </TabsContent>

        <TabsContent
          value="preview"
          forceMount
          className="mt-0 flex-1 min-h-0 overflow-hidden"
          style={{ display: activeTab === "preview" ? "flex" : "none" }}
        >
          <ArtifactPreview steps={steps} activeStepId={activeStepId} />
        </TabsContent>
      </Tabs>
    </motion.div>
  );
}
