import { PanelRight } from "lucide-react";
import { useNavigationStore } from "@/stores/navigationStore";
import { useChatStore } from "@/stores/chatStore";
import { useIsMobile } from "@/hooks/useIsMobile";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { cn } from "@/lib/utils/cn";
import type { CanvasMode } from "@/features/artifacts/CanvasSwitcher";
import { FileDetail } from "@/features/artifacts/FileDetail";
import { RunWorkbench } from "@/features/rlm-workspace/run-workbench/RunWorkbench";
import { MessageInspectorPanel } from "@/features/rlm-workspace/message-inspector/MessageInspectorPanel";
import { isRlmCoreEnabled, isSectionSupported, UNSUPPORTED_SECTION_REASON } from "@/lib/rlm-api";

function UnsupportedState({ sectionLabel, reason }: { sectionLabel: string; reason?: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-8 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-muted">
        <PanelRight className="h-6 w-6 text-muted-foreground" />
      </div>
      <p className="mb-1 text-foreground typo-label">{sectionLabel} unavailable</p>
      <p className="text-muted-foreground typo-caption">
        {reason || "This functionality is currently disabled or unsupported."}
      </p>
    </div>
  );
}

function EmptyCanvas() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-8 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-muted">
        <PanelRight className="h-6 w-6 text-muted-foreground" />
      </div>
      <p className="mb-1 text-foreground typo-label">No active panel</p>
      <p className="text-muted-foreground typo-caption">
        Open a file in Volumes or select an assistant response to inspect it.
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
    case "file-detail":
      return "File Preview";
    case "creation":
      return "Message Inspector";
    default:
      return "Canvas";
  }
}

export function BuilderPanel() {
  const { activeNav, selectedFileNode } = useNavigationStore();
  const runtimeMode = useChatStore((state) => state.runtimeMode);
  const isMobile = useIsMobile();

  const isUnsupportedNav = !isSectionSupported(activeNav);
  const coreReady = isRlmCoreEnabled();
  const showRunWorkbench =
    activeNav === "workspace" && !isUnsupportedNav && runtimeMode === "daytona_pilot";

  const showInspector = activeNav === "workspace" && !isUnsupportedNav;
  const showFileDetail = activeNav === "volumes" && !!selectedFileNode && !isUnsupportedNav;

  const canvasMode: CanvasMode = showInspector
    ? "creation"
    : showFileDetail
      ? "file-detail"
      : "empty";

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/15">
      <div
        className={cn(
          "shrink-0 border-b border-border-subtle/80 bg-card/80 py-3 backdrop-blur-sm",
          isMobile ? "px-4" : "px-4 py-4 md:px-6",
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-foreground">
              {getHeaderLabel(canvasMode)}
            </div>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
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
        ) : showInspector ? (
          <ErrorBoundary name={showRunWorkbench ? "Run Workbench" : "Message Inspector"}>
            {showRunWorkbench ? <RunWorkbench /> : <MessageInspectorPanel />}
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
