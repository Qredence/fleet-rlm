import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ErrorBoundary } from "@/components/product/error-boundary";
import { isRlmCoreEnabled } from "@/lib/rlm-api";
import { useNavigationStore } from "@/stores/navigation-store";
import type { CanvasPanel } from "@/stores/navigation-types";
import { VolumesCanvasPanel } from "@/features/volumes/volumes-canvas-panel";
import { GraphCanvasPanel } from "@/features/workspace/screen/graph-canvas-panel";
import {
  WorkspaceCanvasPanel,
  WorkspaceCanvasUnavailablePanel,
} from "@/features/workspace/screen/workspace-canvas-panel";

const PANEL_LABELS: Record<CanvasPanel, string> = {
  workspace: "Workbench",
  graph: "Graph",
  volumes: "Volumes",
};

function PanelContent({ panel }: { panel: CanvasPanel }) {
  const coreReady = isRlmCoreEnabled();

  switch (panel) {
    case "workspace":
      return coreReady ? (
        <ErrorBoundary name="Workspace Canvas">
          <WorkspaceCanvasPanel />
        </ErrorBoundary>
      ) : (
        <ErrorBoundary name="Mock Mode Active">
          <WorkspaceCanvasUnavailablePanel />
        </ErrorBoundary>
      );
    case "graph":
      return (
        <ErrorBoundary name="Graph Canvas">
          <GraphCanvasPanel />
        </ErrorBoundary>
      );
    case "volumes":
      return (
        <ErrorBoundary name="Volumes Canvas">
          <VolumesCanvasPanel />
        </ErrorBoundary>
      );
    default:
      return null;
  }
}

export function LayoutSidepanel() {
  const { canvasPanel, closeCanvas } = useNavigationStore();

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-border-subtle/80 bg-card/95">
      {/* Header: title + close */}
      <div className="flex shrink-0 items-center justify-between border-b border-border-subtle/80 px-4 py-2">
        <div className="truncate text-sm font-semibold tracking-tight text-foreground">
          {PANEL_LABELS[canvasPanel] ?? "Panel"}
        </div>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7 rounded-lg text-muted-foreground hover:text-foreground"
              aria-label="Close panel"
              onClick={closeCanvas}
            >
              <X className="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="text-xs">
            Close panel
          </TooltipContent>
        </Tooltip>
      </div>

      {/* Panel content */}
      <div className="min-h-0 flex-1 overflow-auto">
        <PanelContent panel={canvasPanel} />
      </div>
    </div>
  );
}

export { LayoutSidepanel as ShellSidepanel };
