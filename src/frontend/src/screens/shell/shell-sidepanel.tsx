import { PanelRight } from "lucide-react";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";

import { useNavigationStore } from "@/stores/navigation-store";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { ErrorBoundary } from "@/components/error-boundary";
import { cn } from "@/lib/utils";
import { VolumesCanvasPanel } from "@/screens/volumes/volumes-canvas-panel";
import {
  WorkspaceCanvasPanel,
  WorkspaceCanvasUnavailablePanel,
} from "@/screens/workspace/workspace-canvas-panel";
import { isRlmCoreEnabled, isSectionSupported, UNSUPPORTED_SECTION_REASON } from "@/lib/rlm-api";
import { getShellPanelMeta } from "@/screens/shell/shell-panel-meta";

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

function EmptyCanvas({ title, description }: { title: string; description: string }) {
  return <EmptyPanel title={title} description={description} />;
}

function navLabel(nav: string): string {
  switch (nav) {
    case "volumes":
      return "Volumes";
    case "settings":
      return "Settings";
    default:
      return "Workbench";
  }
}

export function ShellSidepanel() {
  const activeNav = useNavigationStore((state) => state.activeNav);
  const isMobile = useIsMobile();
  const panelMeta = getShellPanelMeta(activeNav);

  const isUnsupportedNav = !isSectionSupported(activeNav);
  const coreReady = isRlmCoreEnabled();

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-border-subtle/80 bg-card/95">
      <div
        className={cn(
          "shrink-0 border-b border-border-subtle/80 bg-card/95 backdrop-blur-sm",
          isMobile ? "px-4 py-3" : "px-4 py-3",
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <div className="text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-muted-foreground/72">
              {navLabel(activeNav)}
            </div>
            <div className="truncate text-sm font-semibold tracking-tight text-foreground">
              {panelMeta.title}
            </div>
            <p className="max-w-[30ch] text-xs leading-5 text-muted-foreground">
              {panelMeta.description}
            </p>
          </div>
          <div
            className={cn(
              "shrink-0 rounded-full border border-border-subtle/80 bg-background/80 px-2.5 py-1 text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-muted-foreground/72",
              isMobile && "hidden",
            )}
          >
            Panel
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
          <EmptyCanvas title={panelMeta.emptyTitle} description={panelMeta.emptyDescription} />
        )}
      </div>
    </div>
  );
}
