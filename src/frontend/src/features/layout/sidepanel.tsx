import { Database, GitBranch, Terminal, X } from "lucide-react";

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

const PANEL_TABS: { id: CanvasPanel; label: string; icon: typeof Terminal }[] = [
  { id: "workspace", label: "Workbench", icon: Terminal },
  { id: "graph", label: "Graph", icon: GitBranch },
  { id: "volumes", label: "Volumes", icon: Database },
];

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
  const { canvasPanel, setCanvasPanel, closeCanvas } = useNavigationStore();

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-border-subtle/80 bg-card/95">
      {/* Header with panel tab icons on the right */}
      <div className="flex shrink-0 items-center justify-between border-b border-border-subtle/80 px-4 py-2">
        <div className="truncate text-sm font-semibold tracking-tight text-foreground">
          {PANEL_TABS.find((t) => t.id === canvasPanel)?.label ?? "Panel"}
        </div>

        <div className="flex items-center gap-0.5">
          {PANEL_TABS.map(({ id, label, icon: Icon }) => (
            <Tooltip key={id}>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant={canvasPanel === id ? "secondary" : "ghost"}
                  size="icon"
                  className="size-7 rounded-lg"
                  aria-label={label}
                  aria-pressed={canvasPanel === id}
                  onClick={() => setCanvasPanel(id)}
                >
                  <Icon className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="text-xs">
                {label}
              </TooltipContent>
            </Tooltip>
          ))}

          <div className="mx-1 h-4 w-px bg-border-subtle/60" aria-hidden="true" />

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
      </div>

      {/* Panel content */}
      <div className="min-h-0 flex-1 overflow-auto">
        <PanelContent panel={canvasPanel} />
      </div>
    </div>
  );
}

export { LayoutSidepanel as ShellSidepanel };
