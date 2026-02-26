import { X, Brain, PanelRight } from "lucide-react";
import { useCallback } from "react";
import { typo } from "@/lib/config/typo";
import { useNavigation } from "@/hooks/useNavigation";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/components/ui/use-mobile";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { IconButton } from "@/components/ui/icon-button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { cn } from "@/components/ui/utils";
import { CanvasSwitcher, type CanvasMode } from "@/features/CanvasSwitcher";
import { CodeArtifact } from "@/features/CodeArtifact";
import { ArtifactCanvas } from "@/features/artifacts/components/ArtifactCanvas";
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
    case "skills":
      return "Skills";
    case "taxonomy":
      return "Taxonomy";
    case "memory":
      return "Memory";
    case "analytics":
      return "Analytics";
    case "settings":
      return "Settings";
    default:
      return "Chat";
  }
}

function getHeaderLabel(mode: CanvasMode): string {
  switch (mode) {
    case "creation":
      return "Execution";
    case "code-artifact":
      return "Code Sandbox";
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
  const { activeNav, creationPhase, closeCanvas, activeFeatures } =
    useNavigation();
  const { navigateTo } = useAppNavigate();
  const isMobile = useIsMobile();

  const isUnsupportedNav = !isSectionSupported(activeNav);
  const coreReady = isRlmCoreEnabled();

  const showCreation =
    activeNav === "new" && creationPhase !== "idle" && !isUnsupportedNav;
  const showCodeArtifact =
    activeNav === "new" &&
    !showCreation &&
    activeFeatures.has("contextMemory") &&
    !isUnsupportedNav;

  const canvasMode: CanvasMode = showCreation
    ? "creation"
    : showCodeArtifact
      ? "code-artifact"
      : "empty";

  const handleSelectView = useCallback(
    (mode: CanvasMode) => {
      switch (mode) {
        case "taxonomy-graph":
          navigateTo("taxonomy");
          break;
        case "code-artifact":
          navigateTo("new");
          break;
        case "creation":
          navigateTo("new");
          break;
        default:
          break;
      }
    },
    [navigateTo],
  );

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
            <CanvasSwitcher
              canvasMode={canvasMode}
              headerIcon={getHeaderIcon(canvasMode)}
              headerLabel={getHeaderLabel(canvasMode)}
              skills={[]}
              selectedSkill={null}
              onSelectView={handleSelectView}
              onSelectSkill={() => {}}
            />
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
              sectionLabel="Chat"
              reason="FastAPI chat runtime is not available in mock mode. Disable VITE_MOCK_MODE to connect to the backend."
            />
          </ErrorBoundary>
        ) : showCreation ? (
          <ErrorBoundary name="Artifact Canvas">
            <ArtifactCanvas />
          </ErrorBoundary>
        ) : showCodeArtifact ? (
          <ErrorBoundary name="Code Artifact">
            <CodeArtifact />
          </ErrorBoundary>
        ) : (
          <EmptyCanvas />
        )}
      </div>
    </div>
  );
}
