import { EmptyPanel } from "@/components/product/empty-panel";
import { ErrorBoundary } from "@/components/product/error-boundary";
import { isRlmCoreEnabled, isSectionSupported, UNSUPPORTED_SECTION_REASON } from "@/lib/rlm-api";
import { useNavigationStore } from "@/stores/navigation-store";
import { VolumesCanvasPanel } from "@/features/volumes/volumes-canvas-panel";
import {
  WorkspaceCanvasPanel,
  WorkspaceCanvasUnavailablePanel,
} from "@/features/workspace/workspace-canvas-panel";
import { getLayoutPanelMeta } from "./panel-meta";

function UnsupportedPanel({ sectionLabel, reason }: { sectionLabel: string; reason?: string }) {
  return (
    <EmptyPanel
      title={`${sectionLabel} unavailable`}
      description={reason || "This functionality is currently disabled or unsupported."}
      className="h-full rounded-none border-0 bg-transparent"
    />
  );
}

function EmptyCanvas({ title, description }: { title: string; description: string }) {
  return (
    <EmptyPanel
      title={title}
      description={description}
      className="h-full rounded-none border-0 bg-transparent"
    />
  );
}

function navLabel(nav: string): string {
  switch (nav) {
    case "volumes":
      return "Volumes";
    case "optimization":
      return "Optimization";
    case "settings":
      return "Settings";
    default:
      return "Workbench";
  }
}

export function LayoutSidepanel() {
  const activeNav = useNavigationStore((state) => state.activeNav);
  const panelMeta = getLayoutPanelMeta(activeNav);

  const isUnsupportedNav = !isSectionSupported(activeNav);
  const coreReady = isRlmCoreEnabled();

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-border-subtle/80 bg-card/95">
      <div className="shrink-0 border-b border-border-subtle/80 px-4 py-3">
        <div className="truncate text-sm font-semibold tracking-tight text-foreground">
          {panelMeta.title}
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

export { LayoutSidepanel as ShellSidepanel };
