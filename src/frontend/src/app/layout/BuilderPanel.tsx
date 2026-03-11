import { X, PanelRight } from "lucide-react";
import { useCallback } from "react";
import { useNavigationStore } from "@/stores/navigationStore";
import { useChatStore } from "@/stores/chatStore";
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
  CanvasSwitcher,
  type CanvasMode,
} from "@/features/artifacts/CanvasSwitcher";
import { FileDetail } from "@/features/artifacts/FileDetail";
import { RunWorkbench } from "@/features/rlm-workspace/run-workbench/RunWorkbench";
import { MessageInspectorPanel } from "@/features/rlm-workspace/message-inspector/MessageInspectorPanel";
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
    <div className="flex h-full flex-col items-center justify-center px-8 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-muted">
        <PanelRight className="h-6 w-6 text-muted-foreground" />
      </div>
      <p className="mb-1 text-foreground typo-label">
        {sectionLabel} unavailable
      </p>
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
      <p className="mb-1 text-foreground typo-label">
        No active panel
      </p>
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
  const { activeNav, closeCanvas, selectedFileNode } = useNavigationStore();
  const runtimeMode = useChatStore((state) => state.runtimeMode);
  const { navigateTo } = useAppNavigate();
  const isMobile = useIsMobile();

  const isUnsupportedNav = !isSectionSupported(activeNav);
  const coreReady = isRlmCoreEnabled();
  const showRunWorkbench =
    activeNav === "workspace" &&
    !isUnsupportedNav &&
    runtimeMode === "daytona_pilot";

  const showInspector = activeNav === "workspace" && !isUnsupportedNav;
  const showFileDetail =
    activeNav === "volumes" && !!selectedFileNode && !isUnsupportedNav;

  const canvasMode: CanvasMode = showInspector
    ? "creation"
    : showFileDetail
      ? "file-detail"
      : "empty";

  const handleSelectView = useCallback(
    (mode: CanvasMode) => {
      switch (mode) {
        case "volumes-browser":
        case "file-detail":
          navigateTo("volumes");
          break;
        case "creation":
        case "code-artifact":
          navigateTo("workspace");
          break;
        default:
          break;
      }
    },
    [navigateTo],
  );

  return (
    <div className="flex h-full flex-col bg-card">
      <div
        className={cn(
          "shrink-0 border-b border-border-subtle py-3",
          isMobile ? "px-4" : "px-4 py-4 md:px-6",
        )}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            {showInspector ? (
              <div className="min-w-0">
                <div
                  className="truncate text-[11px] uppercase tracking-[0.18em] text-muted-foreground"
                >
                  Workspace
                </div>
                <div className="truncate text-sm font-medium text-foreground">
                  {showRunWorkbench
                    ? "Run Workbench"
                    : "Message Inspector"}
                </div>
              </div>
            ) : (
              <CanvasSwitcher
                canvasMode={canvasMode}
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
                  className={cn("ml-2 shrink-0", isMobile && "touch-target")}
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

      <div className="min-h-0 flex-1">
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
          <ErrorBoundary
            name={showRunWorkbench ? "Run Workbench" : "Message Inspector"}
          >
            {showRunWorkbench ? (
              <RunWorkbench />
            ) : (
              <MessageInspectorPanel />
            )}
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
