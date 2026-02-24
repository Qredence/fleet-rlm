/**
 * TaxonomyGraph — interactive node canvas for the taxonomy view.
 *
 * Features:
 *   - Three layout modes: force-directed, cluster-by-domain, hierarchical tree
 *   - Node drag-to-reposition (click vs drag detection)
 *   - Pan/zoom interactions
 *   - Click-to-select a skill node
 *   - Hover tooltips with skill info
 */
import { useMemo, type CSSProperties } from "react";
import { useReducedMotion } from "motion/react";
import {
  Maximize2,
  ZoomIn,
  ZoomOut,
  Network,
  GitFork,
  TreeDeciduous,
} from "lucide-react";
import { typo } from "@/lib/config/typo";
import { useSkills } from "@/hooks/useSkills";
import type { LayoutMode } from "@/lib/data/graph-layouts";
import { useNavigation } from "@/hooks/useNavigation";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { getDomainColor } from "@/lib/taxonomy/graph";
import { useTaxonomyGraphCanvas } from "@/features/taxonomy/useTaxonomyGraphCanvas";
import { IconButton } from "@/components/ui/icon-button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/components/ui/utils";

const layoutModes: { mode: LayoutMode; label: string; icon: typeof Network }[] =
  [
    { mode: "force", label: "Force", icon: Network },
    { mode: "cluster", label: "Cluster", icon: GitFork },
    { mode: "tree", label: "Tree", icon: TreeDeciduous },
  ];

export function TaxonomyGraph() {
  const { skills: allSkills } = useSkills();
  const { selectedSkillId, openCanvas } = useNavigation();
  const { navigateToSkill } = useAppNavigate();
  const prefersReduced = useReducedMotion();

  const skillMap = useMemo(
    () => new Map(allSkills.map((s) => [s.id, s])),
    [allSkills],
  );

  const {
    canvasRef,
    containerRef,
    layoutMode,
    applyLayout,
    hoveredNode,
    tooltipPos,
    cursorClass,
    domains,
    edgeCount,
    handleMouseDown,
    handleMouseMove,
    handleMouseUp,
    handleMouseLeave,
    handleZoomIn,
    handleZoomOut,
    handleReset,
  } = useTaxonomyGraphCanvas({
    skills: allSkills,
    selectedSkillId,
    prefersReduced,
    onSelectSkill: (skillId) => {
      navigateToSkill("taxonomy", skillId);
      openCanvas();
    },
  });

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden bg-background"
    >
      <canvas
        ref={canvasRef}
        className={cn("w-full h-full", cursorClass)}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
      />

      {hoveredNode &&
        (() => {
          const skill = skillMap.get(hoveredNode.id);
          return (
            <div
              className="fixed z-50 pointer-events-none px-3 py-2 rounded-lg border border-border-subtle shadow-sm"
              style={{
                left: tooltipPos.x + 12,
                top: tooltipPos.y - 8,
                backgroundColor: "var(--popover)",
                color: "var(--popover-foreground)",
                transform: "translateY(-100%)",
              }}
            >
              <p className="text-foreground" style={typo.label}>
                {hoveredNode.displayName}
              </p>
              <p className="text-muted-foreground" style={typo.helper}>
                {hoveredNode.domain} · {hoveredNode.qualityScore}% quality
              </p>
              {skill && (
                <p className="text-muted-foreground" style={typo.micro}>
                  {skill.tags.join(", ")}
                </p>
              )}
              {hoveredNode.pinned && (
                <p className="text-accent mt-1" style={typo.micro}>
                  Pinned — drag to reposition
                </p>
              )}
            </div>
          );
        })()}

      <div
        className="absolute top-4 left-1/2 -translate-x-1/2 flex items-center gap-0.5 p-1 rounded-button border border-border-subtle shadow-sm"
        style={{ backgroundColor: "var(--card)" }}
      >
        {layoutModes.map(({ mode, label, icon: Icon }) => {
          const isActive = layoutMode === mode;
          return (
            <button
              key={mode}
              type="button"
              onClick={() => applyLayout(mode)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-button transition-colors",
                "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
              )}
              style={typo.helper}
              aria-pressed={isActive}
            >
              <Icon className="size-3.5" aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>

      <div className="absolute bottom-4 right-4 flex flex-col gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton
                aria-label="Zoom in"
                onClick={handleZoomIn}
                className="bg-card border border-border-subtle shadow-sm"
              >
                <ZoomIn className="size-4 text-muted-foreground" />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="left">Zoom in</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton
                aria-label="Zoom out"
                onClick={handleZoomOut}
                className="bg-card border border-border-subtle shadow-sm"
              >
                <ZoomOut className="size-4 text-muted-foreground" />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="left">Zoom out</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton
                aria-label="Reset view"
                onClick={handleReset}
                className="bg-card border border-border-subtle shadow-sm"
              >
                <Maximize2 className="size-4 text-muted-foreground" />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="left">Reset view</TooltipContent>
        </Tooltip>
      </div>

      <div
        className="absolute bottom-4 left-4 px-3 py-2.5 rounded-lg border border-border-subtle shadow-sm"
        style={{ backgroundColor: "var(--card)" }}
      >
        <p
          className="text-muted-foreground mb-2 uppercase"
          style={{ ...typo.micro, letterSpacing: "0.06em" } as CSSProperties}
        >
          Domains
        </p>
        <div className="flex flex-col gap-1.5">
          {domains.map((domain) => (
            <div key={domain} className="flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: getDomainColor(domain) }}
              />
              <span className="text-foreground capitalize" style={typo.helper}>
                {domain}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div
        className="absolute top-4 right-4 px-3 py-1.5 rounded-button border border-border-subtle shadow-sm"
        style={{ backgroundColor: "var(--card)" }}
      >
        <span className="text-muted-foreground" style={typo.helper}>
          {allSkills.length} skills · {edgeCount} connections
        </span>
      </div>
    </div>
  );
}
