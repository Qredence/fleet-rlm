import { X, Brain, GitBranch, ArrowLeft, HardDrive } from "lucide-react";
import { useCallback, useEffect } from "react";
import { typo } from "../components/config/typo";
import { useSkills } from "../components/hooks/useSkills";
import { useNavigation } from "../components/hooks/useNavigation";
import { useAppNavigate } from "../components/hooks/useAppNavigate";
import { useIsMobile } from "../components/ui/use-mobile";
import { ErrorBoundary } from "../components/shared/ErrorBoundary";
import { IconButton } from "../components/ui/icon-button";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "../components/ui/tooltip";
import svgPaths from "@/imports/svg-z9gb50zttr";
import { cn } from "../components/ui/utils";
import { motion, AnimatePresence } from "motion/react";
import { useSpring } from "../components/config/motion-config";
import {
  CanvasSwitcher,
  type CanvasMode,
} from "../components/features/CanvasSwitcher";

/* ── Statically imported feature components ───────────────────────── */
/* Converted from React.lazy() to static imports to avoid "Failed to  */
/* fetch dynamically imported module" errors from stale Vite chunks    */
/* in the Figma Make preview environment.                              */
import { SkillDetail } from "../components/features/SkillDetail";
import { CreationPreview } from "../components/features/CreationPreview";
import { TaxonomyGraph } from "../components/features/TaxonomyGraph";
import { CodeArtifact } from "../components/features/CodeArtifact";
import { FileDetail } from "../components/features/FileDetail";
import { ArtifactCanvas } from "../components/artifacts/ArtifactCanvas";
import { isRlmCoreEnabled } from "../lib/rlm-api";

// ── Empty state ─────────────────────────────────────────────────────
function EmptyCanvas() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center mb-4">
        <svg className="w-6 h-6" fill="none" viewBox="0 0 16.33 14.6601">
          <path d={svgPaths.p1f0f5080} fill="var(--muted-foreground)" />
        </svg>
      </div>
      <p className="text-foreground mb-1" style={typo.label}>
        No skill selected
      </p>
      <p className="text-muted-foreground" style={typo.caption}>
        Select a skill from the library or start creating one
      </p>
    </div>
  );
}

// ── Header label helpers ────────────────────────────────────────────

function getHeaderLabel(
  mode: CanvasMode,
  skillName?: string,
  fileName?: string,
): string {
  switch (mode) {
    case "creation":
      return "SKILL.md";
    case "detail":
      return skillName ?? "Skill";
    case "taxonomy-graph":
      return "Skill Graph";
    case "code-artifact":
      return "Code Sandbox";
    case "file-detail":
      return fileName ?? "File";
    default:
      return "Canvas";
  }
}

function getHeaderIcon(mode: CanvasMode) {
  switch (mode) {
    case "taxonomy-graph":
      return (
        <GitBranch
          className="size-3.5 text-muted-foreground shrink-0"
          aria-hidden="true"
        />
      );
    case "code-artifact":
      return (
        <Brain className="size-3.5 text-accent shrink-0" aria-hidden="true" />
      );
    case "file-detail":
      return (
        <HardDrive
          className="size-3.5 text-chart-4 shrink-0"
          aria-hidden="true"
        />
      );
    default:
      return null;
  }
}

// ── Main BuilderPanel ───────────────────────────────────────────────
/**
 * BuilderPanel — multi-purpose canvas panel.
 *
 * Content routing priority:
 *   1. Creation Preview — active creation flow (`new` tab + phase ≠ idle)
 *   2. Skill Detail — specific skill selected from library/taxonomy
 *   3. File Detail — file selected from filesystem view (non-skill)
 *   4. Taxonomy Graph — on taxonomy tab with no skill/file selected
 *   5. Code Artifact — Context Memory feature active on `new` tab
 *   6. Empty Canvas — fallback
 *
 * All state consumed from NavigationContext — zero props.
 */
export function BuilderPanel() {
  const {
    activeNav,
    selectedSkillId,
    selectedFileNode,
    creationPhase,
    closeCanvas,
    activeFeatures,
    selectSkill,
    selectFile,
    toggleFeature,
  } = useNavigation();
  const { navigateTo, navigateToSkill, navigateToSection } = useAppNavigate();
  const isMobile = useIsMobile();
  const snappySpring = useSpring("snappy");

  const { skills: allSkills } = useSkills();
  const selectedSkill = selectedSkillId
    ? allSkills.find((s) => s.id === selectedSkillId)
    : null;

  // Content routing — determine which canvas mode to display
  const showCreation = activeNav === "new" && creationPhase !== "idle";
  const showDetail =
    (activeNav === "skills" || activeNav === "taxonomy") && selectedSkill;
  const showFileDetail =
    activeNav === "taxonomy" && !selectedSkill && !!selectedFileNode;
  const showTaxonomyGraph =
    activeNav === "taxonomy" && !selectedSkill && !selectedFileNode;
  const showCodeArtifact =
    !showCreation &&
    !showDetail &&
    !showFileDetail &&
    !showTaxonomyGraph &&
    activeFeatures.has("contextMemory");

  // Determine mode for header labeling
  const canvasMode: CanvasMode = showCreation
    ? "creation"
    : showDetail
      ? "detail"
      : showFileDetail
        ? "file-detail"
        : showTaxonomyGraph
          ? "taxonomy-graph"
          : showCodeArtifact
            ? "code-artifact"
            : "empty";

  const headerIcon = getHeaderIcon(canvasMode);

  // Whether back-to-graph/tree is available
  const canGoBack =
    activeNav === "taxonomy" && (!!showDetail || !!showFileDetail);

  const handleBack = useCallback(() => {
    if (showDetail) {
      navigateToSection("taxonomy");
      selectSkill(null);
    } else if (showFileDetail) {
      selectFile(null);
    }
  }, [showDetail, showFileDetail, navigateToSection, selectSkill, selectFile]);

  // ── Canvas switcher callbacks ──────────────────────────────────────

  const handleSelectView = useCallback(
    (mode: CanvasMode) => {
      switch (mode) {
        case "taxonomy-graph":
          // Navigate to taxonomy tab — deselects skill/file so graph shows
          selectSkill(null);
          selectFile(null);
          navigateTo("taxonomy");
          break;
        case "code-artifact":
          // Toggle contextMemory feature on — opens code sandbox
          if (!activeFeatures.has("contextMemory")) {
            toggleFeature("contextMemory");
          }
          // Ensure we're on the new tab for code artifact
          if (activeNav !== "new") {
            navigateTo("new");
          }
          break;
        case "creation":
          // Go to creation flow
          navigateTo("new");
          break;
        default:
          break;
      }
    },
    [
      activeNav,
      activeFeatures,
      navigateTo,
      selectSkill,
      selectFile,
      toggleFeature,
    ],
  );

  const handleSelectSkill = useCallback(
    (skillId: string) => {
      // Navigate to skills section with the chosen skill
      navigateToSkill("skills", skillId);
    },
    [navigateToSkill],
  );

  // Escape key → deselect when viewing detail
  useEffect(() => {
    if (!canGoBack) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        handleBack();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canGoBack, handleBack]);

  return (
    <div className="flex flex-col h-full bg-card">
      {/* Header */}
      <div
        className={cn(
          "py-3 border-b border-border-subtle shrink-0",
          isMobile ? "px-4" : "px-4 md:px-6 py-4",
        )}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            {/* Back button — visible when viewing a skill/file on taxonomy tab */}
            <AnimatePresence>
              {canGoBack && (
                <motion.div
                  key="back-btn"
                  initial={{ opacity: 0, x: -8, scale: 0.85 }}
                  animate={{ opacity: 1, x: 0, scale: 1 }}
                  exit={{ opacity: 0, x: -8, scale: 0.85 }}
                  transition={snappySpring}
                  className="flex items-center"
                >
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="inline-flex">
                        <IconButton
                          className={cn("shrink-0", isMobile && "touch-target")}
                          onClick={handleBack}
                          aria-label="Back"
                        >
                          <ArrowLeft className="size-5 text-muted-foreground" />
                        </IconButton>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">
                      {showFileDetail ? "Back to filesystem" : "Back to graph"}
                    </TooltipContent>
                  </Tooltip>
                </motion.div>
              )}
            </AnimatePresence>
            <CanvasSwitcher
              canvasMode={canvasMode}
              headerIcon={headerIcon}
              headerLabel={getHeaderLabel(
                canvasMode,
                selectedSkill?.displayName,
                selectedFileNode?.name,
              )}
              version={
                showDetail && selectedSkill ? selectedSkill.version : undefined
              }
              selectedSkill={selectedSkill}
              skills={allSkills}
              onSelectView={handleSelectView}
              onSelectSkill={handleSelectSkill}
            />
          </div>
          {/* Close — 44px touch target on mobile */}
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

      {/* Content */}
      <div className="flex-1 min-h-0">
        {showCreation && (
          <ErrorBoundary
            name={isRlmCoreEnabled() ? "Artifact Canvas" : "Creation Preview"}
          >
            {isRlmCoreEnabled() ? (
              <ArtifactCanvas />
            ) : (
              <CreationPreview phase={creationPhase} />
            )}
          </ErrorBoundary>
        )}
        {showDetail && (
          <ErrorBoundary name="Skill Detail">
            <SkillDetail skill={selectedSkill!} />
          </ErrorBoundary>
        )}
        {showFileDetail && selectedFileNode && (
          <ErrorBoundary name="File Detail">
            <FileDetail file={selectedFileNode} />
          </ErrorBoundary>
        )}
        {showTaxonomyGraph && (
          <ErrorBoundary name="Taxonomy Graph">
            <TaxonomyGraph />
          </ErrorBoundary>
        )}
        {showCodeArtifact && (
          <ErrorBoundary name="Code Artifact">
            <CodeArtifact />
          </ErrorBoundary>
        )}
        {!showCreation &&
          !showDetail &&
          !showFileDetail &&
          !showTaxonomyGraph &&
          !showCodeArtifact && <EmptyCanvas />}
      </div>
    </div>
  );
}
