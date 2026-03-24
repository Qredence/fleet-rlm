import { PanelRight } from "lucide-react";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";

import { useNavigationStore } from "@/stores/navigationStore";
import { useIsMobile } from "@/hooks/useIsMobile";
import { ErrorBoundary } from "@/components/error-boundary";
import { cn } from "@/lib/utils";
import { VolumesCanvasPanel } from "@/screens/volumes/volumes-canvas-panel";
import {
  WorkspaceCanvasPanel,
  WorkspaceCanvasUnavailablePanel,
} from "@/screens/workspace/workspace-canvas-panel";
import { isRlmCoreEnabled, isSectionSupported, UNSUPPORTED_SECTION_REASON } from "@/lib/rlm-api";

type CanvasMode = "workspace" | "volumes" | "empty";

function EmptyPanel({ title, description }: { title: string; description: string }) {
  return (
    <Empty className="h-full rounded-none border-0 bg-transparent">
      <EmptyMedia variant="icon">
        <PanelRight />
      </EmptyMedia>
      <EmptyContent>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyContent>
    </Empty>
  );
}

function UnsupportedPanel({ sectionLabel, reason }: { sectionLabel: string; reason?: string }) {
  return (
    <EmptyPanel
      title={`${sectionLabel} unavailable`}
      description={reason || "This functionality is currently disabled or unsupported."}
    />
  );
}

function EmptyCanvas() {
  return (
    <EmptyPanel
      title="No active panel"
      description="Open a file in Volumes or select an assistant response to inspect it."
    />
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
    case "volumes":
      return "Preview";
    case "workspace":
      return "Canvas";
    default:
      return "Canvas";
  }
}

export function ShellSidepanel() {
  const activeNav = useNavigationStore((state) => state.activeNav);
  const isMobile = useIsMobile();

  const isUnsupportedNav = !isSectionSupported(activeNav);
  const coreReady = isRlmCoreEnabled();
  const canvasMode: CanvasMode =
    activeNav === "workspace" ? "workspace" : activeNav === "volumes" ? "volumes" : "empty";

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-border-subtle/80 bg-card/95">
      <div
        className={cn(
          "shrink-0 border-b border-border-subtle/80 bg-card/95 backdrop-blur-sm",
          isMobile ? "px-4 py-3" : "px-4 py-3",
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium tracking-tight text-foreground">
              {getHeaderLabel(canvasMode)}
            </div>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {isUnsupportedNav ? (
          <ErrorBoundary name="Unsupported Section">
            <UnsupportedPanel
              sectionLabel={navLabel(activeNav)}
              reason={UNSUPPORTED_SECTION_REASON}
            />
          </ErrorBoundary>
        ) : activeNav === "workspace" && !coreReady ? (
          <ErrorBoundary name="Mock Mode Active">
            <WorkspaceCanvasUnavailablePanel />
          </ErrorBoundary>
        ) : activeNav === "workspace" ? (
          <ErrorBoundary name="Workspace Canvas">
            <WorkspaceCanvasPanel />
          </ErrorBoundary>
        ) : activeNav === "volumes" ? (
          <ErrorBoundary name="Volumes Canvas">
            <VolumesCanvasPanel />
          </ErrorBoundary>
        ) : (
          <EmptyCanvas />
        )}
      </div>
    </div>
  );
}
