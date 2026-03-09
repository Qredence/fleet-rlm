import {
  X,
  Brain,
  PanelRight,
  GitBranch,
  TerminalSquare,
  ListTree,
  FileCode2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { typo } from "@/lib/config/typo";
import { useNavigationStore } from "@/stores/navigationStore";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/hooks/useIsMobile";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { IconButton } from "@/components/ui/icon-button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils/cn";
import {
  AnimatedTabs,
  type AnimatedTabItem,
} from "@/components/ui/animated-tabs";
import {
  CanvasSwitcher,
  type CanvasMode,
} from "@/features/artifacts/CanvasSwitcher";
import { FileDetail } from "@/features/artifacts/FileDetail";
import {
  ArtifactCanvas,
  type ArtifactTab,
} from "@/components/domain/artifacts/ArtifactCanvas";
import { useArtifactStore } from "@/stores/artifactStore";
import {
  isRlmCoreEnabled,
  isSectionSupported,
  UNSUPPORTED_SECTION_REASON,
} from "@/lib/rlm-api";

function UnsupportedState({
  sectionLabel,
  reason,
}: {
  sectionLabel: string;
  reason?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center mb-4">
        <PanelRight className="w-6 h-6 text-muted-foreground" />
      </div>
      <p className="text-foreground mb-1" style={typo.label}>
        {sectionLabel} unavailable
      </p>
      <p className="text-muted-foreground" style={typo.caption}>
        {reason || "This functionality is currently disabled or unsupported."}
      </p>
    </div>
  );
}

function EmptyCanvas() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center mb-4">
        <PanelRight className="w-6 h-6 text-muted-foreground" />
      </div>
      <p className="text-foreground mb-1" style={typo.label}>
        No active canvas
      </p>
      <p className="text-muted-foreground" style={typo.caption}>
        Start a chat request to stream artifacts from the FastAPI backend.
      </p>
    </div>
  );
}

function navLabel(nav: string): string {
  switch (nav) {
    case "volumes":
      return "Volumes";
    case "settings":
      return "Settings";
    default:
      return "RLM Workspace";
  }
}

function getHeaderLabel(mode: CanvasMode): string {
  switch (mode) {
    case "creation":
      return "Execution";
    case "code-artifact":
      return "Code Sandbox";
    case "file-detail":
      return "File Preview";
    default:
      return "Canvas";
  }
}

function getHeaderIcon(mode: CanvasMode) {
  if (mode === "code-artifact") {
    return (
      <Brain className="size-3.5 text-accent shrink-0" aria-hidden="true" />
    );
  }
  return null;
}

export function BuilderPanel() {
  const { activeNav, closeCanvas, selectedFileNode } = useNavigationStore();
  const { navigateTo } = useAppNavigate();
  const isMobile = useIsMobile();
  const steps = useArtifactStore((state) => state.steps);

  const [activeArtifactTab, setActiveArtifactTab] =
    useState<ArtifactTab>("graph");
  const [hasUserSelectedArtifactTab, setHasUserSelectedArtifactTab] =
    useState(false);

  const artifactSummary = useMemo(() => {
    const counts = steps.reduce(
      (acc, step) => {
        acc[step.type] += 1;
        return acc;
      },
      { llm: 0, repl: 0, tool: 0, memory: 0, output: 0 },
    );

    return {
      stepCount: steps.length,
      hasTimeline: steps.length > 0,
      hasRepl: counts.repl > 0 || counts.tool > 0,
      hasPreview: counts.output > 0,
    };
  }, [steps]);

  const artifactTabs = useMemo<AnimatedTabItem<ArtifactTab>[]>(
    () => [
      {
        id: "graph",
        label: "Graph",
        icon: <GitBranch className="size-3.5" />,
      },
      {
        id: "repl",
        label: "REPL",
        icon: <TerminalSquare className="size-3.5" />,
        disabled: !artifactSummary.hasRepl,
      },
      {
        id: "timeline",
        label: "Timeline",
        icon: <ListTree className="size-3.5" />,
        disabled: !artifactSummary.hasTimeline,
      },
      {
        id: "preview",
        label: "Preview",
        icon: <FileCode2 className="size-3.5" />,
        disabled: !artifactSummary.hasPreview,
      },
    ],
    [
      artifactSummary.hasPreview,
      artifactSummary.hasRepl,
      artifactSummary.hasTimeline,
    ],
  );

  const isUnsupportedNav = !isSectionSupported(activeNav);
  const coreReady = isRlmCoreEnabled();

  const showCreation = activeNav === "workspace" && !isUnsupportedNav;
  const showFileDetail =
    activeNav === "volumes" && !!selectedFileNode && !isUnsupportedNav;

  const canvasMode: CanvasMode = showCreation
    ? "creation"
    : showFileDetail
      ? "file-detail"
      : "empty";

  const handleSelectView = useCallback(
    (mode: CanvasMode) => {
      switch (mode) {
        case "volumes-browser":
          navigateTo("volumes");
          break;
        case "code-artifact":
          navigateTo("workspace");
          break;
        case "creation":
          navigateTo("workspace");
          break;
        case "file-detail":
          navigateTo("volumes");
          break;
        default:
          break;
      }
    },
    [navigateTo],
  );

  useEffect(() => {
    if (!showCreation) {
      setActiveArtifactTab("graph");
      setHasUserSelectedArtifactTab(false);
      return;
    }

    if (!hasUserSelectedArtifactTab) {
      setActiveArtifactTab(
        artifactSummary.stepCount > 0 ? "timeline" : "graph",
      );
    }
  }, [artifactSummary.stepCount, hasUserSelectedArtifactTab, showCreation]);

  useEffect(() => {
    if (!showCreation) return;

    if (activeArtifactTab === "timeline" && !artifactSummary.hasTimeline) {
      setActiveArtifactTab("graph");
      return;
    }

    if (activeArtifactTab === "repl" && !artifactSummary.hasRepl) {
      setActiveArtifactTab(artifactSummary.hasTimeline ? "timeline" : "graph");
      return;
    }

    if (activeArtifactTab === "preview" && !artifactSummary.hasPreview) {
      setActiveArtifactTab(artifactSummary.hasTimeline ? "timeline" : "graph");
    }
  }, [
    activeArtifactTab,
    artifactSummary.hasPreview,
    artifactSummary.hasRepl,
    artifactSummary.hasTimeline,
    showCreation,
  ]);

  return (
    <div className="flex flex-col h-full bg-card">
      <div
        className={cn(
          "py-3 border-b border-border-subtle shrink-0",
          isMobile ? "px-4" : "px-4 md:px-6 py-4",
        )}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            {showCreation ? (
              <AnimatedTabs
                value={activeArtifactTab}
                tabs={artifactTabs}
                onValueChange={(value: ArtifactTab) => {
                  setHasUserSelectedArtifactTab(true);
                  setActiveArtifactTab(value);
                }}
                className="min-w-0"
              />
            ) : (
              <CanvasSwitcher
                canvasMode={canvasMode}
                headerIcon={getHeaderIcon(canvasMode)}
                headerLabel={getHeaderLabel(canvasMode)}
                skills={[]}
                selectedSkill={null}
                onSelectView={handleSelectView}
                onSelectSkill={() => {}}
              />
            )}
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <IconButton
                  className={cn("shrink-0 ml-2", isMobile && "touch-target")}
                  onClick={closeCanvas}
                  aria-label="Close panel"
                >
                  <X className="size-5 text-muted-foreground" />
                </IconButton>
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom">Close panel</TooltipContent>
          </Tooltip>
        </div>
      </div>

      <div className="flex-1 min-h-0">
        {isUnsupportedNav ? (
          <ErrorBoundary name="Unsupported Section">
            <UnsupportedState
              sectionLabel={navLabel(activeNav)}
              reason={UNSUPPORTED_SECTION_REASON}
            />
          </ErrorBoundary>
        ) : !coreReady ? (
          <ErrorBoundary name="Mock Mode Active">
            <UnsupportedState
              sectionLabel="RLM Workspace"
              reason="The RLM Workspace requires a live FastAPI runtime. Disable VITE_MOCK_MODE to connect to the backend."
            />
          </ErrorBoundary>
        ) : showCreation ? (
          <ErrorBoundary name="Artifact Canvas">
            <ArtifactCanvas
              activeTab={activeArtifactTab}
              onTabChange={setActiveArtifactTab}
              showTabs={false}
            />
          </ErrorBoundary>
        ) : showFileDetail && selectedFileNode ? (
          <ErrorBoundary name="File Detail">
            <FileDetail file={selectedFileNode} />
          </ErrorBoundary>
        ) : (
          <EmptyCanvas />
        )}
      </div>
    </div>
  );
}
