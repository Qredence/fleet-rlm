/**
 * TaxonomyGraph — interactive force-directed node canvas for the taxonomy view.
 *
 * Features:
 *   - Three layout modes: force-directed, cluster-by-domain, hierarchical tree
 *   - Node drag-to-reposition (click vs drag detection with 3px threshold)
 *   - Pan (drag on empty space) and zoom (scroll wheel)
 *   - Click-to-select a skill node (updates NavigationContext)
 *   - Hover tooltips with skill info
 *   - All colours from CSS variables (fully re-themeable)
 *   - prefers-reduced-motion: disables continuous simulation
 *
 * Uses HTML5 Canvas API directly (per guidelines — no konva).
 */
import {
  useRef,
  useEffect,
  useCallback,
  useState,
  useMemo,
  type CSSProperties,
} from "react";
import { useReducedMotion } from "motion/react";
import {
  Maximize2,
  ZoomIn,
  ZoomOut,
  Network,
  GitFork,
  TreeDeciduous,
} from "lucide-react";
import { typo } from "../config/typo";
import { useSkills } from "../hooks/useSkills";
import type { Skill } from "../data/types";
import { useNavigation } from "../hooks/useNavigation";
import { useAppNavigate } from "../hooks/useAppNavigate";
import { IconButton } from "../ui/icon-button";
import { Tooltip, TooltipTrigger, TooltipContent } from "../ui/tooltip";
import { cn } from "../ui/utils";
import {
  type GraphNode,
  type GraphEdge,
  type LayoutMode,
  simulateForces,
  applyClusterLayout,
  applyTreeLayout,
} from "../data/graph-layouts";

// ── Domain color map (CSS variable refs) ────────────────────────────

const domainColorMap: Record<string, string> = {
  analytics: "--chart-1",
  development: "--chart-2",
  nlp: "--chart-3",
  devops: "--chart-4",
};

function getDomainColor(domain: string): string {
  return `var(${domainColorMap[domain] ?? "--chart-5"})`;
}

// ── Resolve CSS variable to actual color string ─────────────────────

function resolveCSSVar(varName: string, el: HTMLElement): string {
  const styles = getComputedStyle(el);
  const raw = varName.replace(/^var\(/, "").replace(/\)$/, "");
  return styles.getPropertyValue(raw).trim() || "rgb(143, 143, 143)";
}

function withAlpha(color: string, alpha: number): string {
  const rgbaMatch = color.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (rgbaMatch) {
    const [, r = "143", g = "143", b = "143"] = rgbaMatch;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  const hexMatch = color.match(/^#([0-9a-f]{6})$/i);
  if (hexMatch) {
    const hex = hexMatch[1];
    if (!hex) return color;
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return color;
}

// ── Build graph data from skills ────────────────────────────────────

function buildGraph(skills: Skill[]): {
  nodes: GraphNode[];
  edges: GraphEdge[];
} {
  const nodes: GraphNode[] = skills.map((skill, i) => {
    const angle = (i / skills.length) * Math.PI * 2;
    const spread = 160;
    return {
      id: skill.id,
      domain: skill.domain,
      displayName: skill.displayName,
      qualityScore: skill.qualityScore,
      tags: skill.tags,
      dependencies: skill.dependencies,
      x: Math.cos(angle) * spread + (Math.random() - 0.5) * 40,
      y: Math.sin(angle) * spread + (Math.random() - 0.5) * 40,
      vx: 0,
      vy: 0,
      radius: 18 + (skill.qualityScore / 100) * 14,
      pinned: false,
    };
  });

  const edges: GraphEdge[] = [];
  for (let i = 0; i < skills.length; i++) {
    for (let j = i + 1; j < skills.length; j++) {
      const a = skills[i];
      const b = skills[j];
      if (!a || !b) continue;
      let weight = 0;
      if (a.domain === b.domain) weight += 2;
      const sharedTags = a.tags.filter((t) => b.tags.includes(t));
      weight += sharedTags.length;
      if (a.dependencies.includes(b.name) || b.dependencies.includes(a.name)) {
        weight += 3;
      }
      if (weight > 0) {
        edges.push({ source: a.id, target: b.id, weight });
      }
    }
  }

  return { nodes, edges };
}

// ── Drag threshold (px) — differentiates click from drag ────────────
const DRAG_THRESHOLD = 3;

// ── Layout mode labels ──────────────────────────────────────────────

const layoutModes: { mode: LayoutMode; label: string; icon: typeof Network }[] =
  [
    { mode: "force", label: "Force", icon: Network },
    { mode: "cluster", label: "Cluster", icon: GitFork },
    { mode: "tree", label: "Tree", icon: TreeDeciduous },
  ];

// ── Main component ──────────────────────────────────────────────────

export function TaxonomyGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const { skills: allSkills } = useSkills();
  const skillMap = useMemo(
    () => new Map<string, Skill>(allSkills.map((s) => [s.id, s])),
    [allSkills],
  );
  const graphRef = useRef(buildGraph(allSkills));
  const animRef = useRef<number>(0);
  const alphaRef = useRef(1);
  const prefersReduced = useReducedMotion();
  const { selectedSkillId, openCanvas } = useNavigation();
  const { navigateToSkill } = useAppNavigate();

  // Layout mode
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("force");
  const layoutModeRef = useRef(layoutMode);
  layoutModeRef.current = layoutMode;

  // Pan/zoom state
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const transformRef = useRef(transform);
  transformRef.current = transform;

  // Interaction refs
  const panRef = useRef<{
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const nodeDragRef = useRef<{
    node: GraphNode;
    startClientX: number;
    startClientY: number;
    isDragging: boolean;
  } | null>(null);

  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  // ── Resolve colors from CSS variables ─────────────────────────────
  const colorsRef = useRef<{
    bg: string;
    fg: string;
    muted: string;
    mutedFg: string;
    accent: string;
    borderSubtle: string;
    domainColors: Record<string, string>;
    fontFamily: string;
    labelFg: string;
  } | null>(null);

  const resolveColors = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const styles = getComputedStyle(el);
    colorsRef.current = {
      bg: resolveCSSVar("var(--background)", el),
      fg: resolveCSSVar("var(--foreground)", el),
      muted: resolveCSSVar("var(--muted)", el),
      mutedFg: resolveCSSVar("var(--muted-foreground)", el),
      accent: resolveCSSVar("var(--accent)", el),
      borderSubtle: resolveCSSVar("var(--border-subtle)", el),
      domainColors: Object.fromEntries(
        Object.entries(domainColorMap).map(([domain, varName]) => [
          domain,
          resolveCSSVar(`var(${varName})`, el),
        ]),
      ),
      fontFamily:
        styles.getPropertyValue("--font-family").trim() ||
        '-apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif',
      labelFg: resolveCSSVar("var(--primary-foreground)", el),
    };
  }, []);

  // ── Draw frame ────────────────────────────────────────────────────
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (!colorsRef.current) resolveColors();
    const colors = colorsRef.current;
    if (!colors) return;

    const { nodes, edges } = graphRef.current;
    const t = transformRef.current;
    const w = canvas.width;
    const h = canvas.height;
    const dpr = window.devicePixelRatio || 1;
    const mode = layoutModeRef.current;

    ctx.clearRect(0, 0, w, h);
    ctx.save();

    const cw = w / dpr;
    const ch = h / dpr;
    ctx.scale(dpr, dpr);
    ctx.translate(cw / 2, ch / 2);
    ctx.scale(t.scale, t.scale);
    ctx.translate(t.x, t.y);

    const nodeMap = new Map(nodes.map((n) => [n.id, n]));

    // Draw edges
    for (const edge of edges) {
      const a = nodeMap.get(edge.source);
      const b = nodeMap.get(edge.target);
      if (!a || !b) continue;

      const edgeAlpha = Math.min(edge.weight * 0.12, 0.5);
      const edgeColor =
        a.domain === b.domain
          ? (colors.domainColors[a.domain] ?? colors.mutedFg)
          : colors.mutedFg;

      ctx.beginPath();
      if (mode === "tree") {
        // Curved edges for tree layout
        const midY = (a.y + b.y) / 2;
        ctx.moveTo(a.x, a.y);
        ctx.bezierCurveTo(a.x, midY, b.x, midY, b.x, b.y);
      } else {
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
      }
      ctx.strokeStyle = withAlpha(edgeColor, edgeAlpha);
      ctx.lineWidth = Math.max(1, edge.weight * 0.5) / t.scale;
      ctx.stroke();
    }

    // Draw nodes
    const draggingId = nodeDragRef.current?.isDragging
      ? nodeDragRef.current.node.id
      : null;

    for (const node of nodes) {
      const isSelected = node.id === selectedSkillId;
      const isHovered = hoveredNode?.id === node.id;
      const isDragged = node.id === draggingId;
      const domainColor = colors.domainColors[node.domain] ?? colors.mutedFg;

      // Outer glow for selected/hovered/dragged
      if (isSelected || isHovered || isDragged) {
        ctx.beginPath();
        ctx.arc(
          node.x,
          node.y,
          node.radius + (isDragged ? 6 : 4),
          0,
          Math.PI * 2,
        );
        ctx.fillStyle = isDragged
          ? withAlpha(colors.accent, 0.15)
          : isSelected
            ? withAlpha(colors.accent, 0.2)
            : withAlpha(domainColor, 0.12);
        ctx.fill();
      }

      // Pinned indicator ring
      if (node.pinned && !isDragged) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.radius + 3, 0, Math.PI * 2);
        ctx.strokeStyle = withAlpha(colors.accent, 0.4);
        ctx.lineWidth = 1.5 / t.scale;
        ctx.setLineDash([3 / t.scale, 3 / t.scale]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Node circle
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
      ctx.fillStyle = withAlpha(
        domainColor,
        isSelected || isDragged ? 1 : 0.85,
      );
      ctx.fill();

      // Border
      ctx.strokeStyle = isSelected || isDragged ? colors.accent : domainColor;
      ctx.lineWidth = (isSelected || isDragged ? 2.5 : 1.5) / t.scale;
      ctx.stroke();

      // Label — using design system font stack from CSS var(--font-family)
      const labelFontSize = Math.max(9, 11 / t.scale);
      ctx.font = `500 ${labelFontSize}px ${colors.fontFamily}`;
      ctx.fillStyle = colors.labelFg;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      const maxWidth = node.radius * 1.6;
      let label = node.displayName;
      if (ctx.measureText(label).width > maxWidth) {
        while (
          label.length > 3 &&
          ctx.measureText(label + "\u2026").width > maxWidth
        ) {
          label = label.slice(0, -1);
        }
        label += "\u2026";
      }
      ctx.fillText(label, node.x, node.y);
    }

    ctx.restore();
  }, [hoveredNode, selectedSkillId, resolveColors]);

  // ── Animation loop ────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = container.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      resolveColors();
    };

    resize();
    const obs = new ResizeObserver(resize);
    obs.observe(container);

    const tick = () => {
      const mode = layoutModeRef.current;
      // Only run force sim in force mode (or for settling after layout switch)
      if (mode === "force" && alphaRef.current > 0.005 && !prefersReduced) {
        simulateForces(
          graphRef.current.nodes,
          graphRef.current.edges,
          canvas.width,
          canvas.height,
          alphaRef.current,
        );
        alphaRef.current *= 0.995;
      }
      draw();
      animRef.current = requestAnimationFrame(tick);
    };

    if (prefersReduced) {
      // Settle immediately for reduced motion
      for (let i = 0; i < 200; i++) {
        simulateForces(
          graphRef.current.nodes,
          graphRef.current.edges,
          canvas.width,
          canvas.height,
          1 - i / 200,
        );
      }
      draw();
    } else {
      animRef.current = requestAnimationFrame(tick);
    }

    // Re-resolve colors when theme changes
    const htmlEl = document.documentElement;
    const mutObs = new MutationObserver(() => resolveColors());
    mutObs.observe(htmlEl, { attributes: true, attributeFilter: ["class"] });

    return () => {
      cancelAnimationFrame(animRef.current);
      obs.disconnect();
      mutObs.disconnect();
    };
  }, [draw, prefersReduced, resolveColors]);

  // ── Layout mode changes ───────────────────────────────────────────
  const applyLayout = useCallback(
    (mode: LayoutMode) => {
      setLayoutMode(mode);
      const { nodes, edges } = graphRef.current;

      // Unpin all nodes on layout change
      for (const n of nodes) n.pinned = false;

      if (mode === "cluster") {
        applyClusterLayout(nodes);
      } else if (mode === "tree") {
        applyTreeLayout(nodes, edges);
      } else {
        // Force: reheat simulation
        alphaRef.current = 1;
      }

      // For non-force modes with reduced motion, just redraw
      if (mode !== "force" && prefersReduced) {
        draw();
      }
    },
    [prefersReduced, draw],
  );

  // ── Hit test ──────────────────────────────────────────────────────
  const clientToGraph = useCallback(
    (clientX: number, clientY: number): { gx: number; gy: number } => {
      const canvas = canvasRef.current;
      if (!canvas) return { gx: 0, gy: 0 };
      const rect = canvas.getBoundingClientRect();
      const t = transformRef.current;
      const gx = (clientX - rect.left - rect.width / 2) / t.scale - t.x;
      const gy = (clientY - rect.top - rect.height / 2) / t.scale - t.y;
      return { gx, gy };
    },
    [],
  );

  const hitTest = useCallback(
    (clientX: number, clientY: number): GraphNode | null => {
      const { gx, gy } = clientToGraph(clientX, clientY);
      for (const node of graphRef.current.nodes) {
        const dx = gx - node.x;
        const dy = gy - node.y;
        if (dx * dx + dy * dy <= (node.radius + 4) * (node.radius + 4)) {
          return node;
        }
      }
      return null;
    },
    [clientToGraph],
  );

  // ── Mouse handlers ────────────────────────────────────────────────

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const node = hitTest(e.clientX, e.clientY);
      if (node) {
        // Start potential node drag
        nodeDragRef.current = {
          node,
          startClientX: e.clientX,
          startClientY: e.clientY,
          isDragging: false,
        };
        return;
      }
      // Start pan
      panRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        originX: transformRef.current.x,
        originY: transformRef.current.y,
      };
    },
    [hitTest],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      // Node dragging
      if (nodeDragRef.current) {
        const nd = nodeDragRef.current;
        const dx = e.clientX - nd.startClientX;
        const dy = e.clientY - nd.startClientY;

        if (!nd.isDragging && Math.sqrt(dx * dx + dy * dy) > DRAG_THRESHOLD) {
          nd.isDragging = true;
          nd.node.pinned = true;
          // Reheat simulation so other nodes adjust
          if (layoutMode === "force") {
            alphaRef.current = Math.max(alphaRef.current, 0.3);
          }
        }

        if (nd.isDragging) {
          const { gx, gy } = clientToGraph(e.clientX, e.clientY);
          nd.node.x = gx;
          nd.node.y = gy;
          nd.node.vx = 0;
          nd.node.vy = 0;
        }
        return;
      }

      // Panning
      if (panRef.current) {
        const dx =
          (e.clientX - panRef.current.startX) / transformRef.current.scale;
        const dy =
          (e.clientY - panRef.current.startY) / transformRef.current.scale;
        setTransform((prev) => ({
          ...prev,
          x: panRef.current!.originX + dx,
          y: panRef.current!.originY + dy,
        }));
        return;
      }

      // Hover detection
      const node = hitTest(e.clientX, e.clientY);
      setHoveredNode(node);
      if (node) {
        setTooltipPos({ x: e.clientX, y: e.clientY });
      }
    },
    [hitTest, clientToGraph, layoutMode],
  );

  const handleMouseUp = useCallback(
    (_e: React.MouseEvent) => {
      // Node drag completion
      if (nodeDragRef.current) {
        const nd = nodeDragRef.current;
        if (!nd.isDragging) {
          // Was a click, not a drag — navigate to skill detail
          navigateToSkill("taxonomy", nd.node.id);
          openCanvas();
        }
        // Node stays pinned after drag so it doesn't float away
        nodeDragRef.current = null;
        return;
      }
      panRef.current = null;
    },
    [navigateToSkill, openCanvas],
  );

  const handleMouseLeave = useCallback(() => {
    // If dragging a node, finish the drag (keep it pinned)
    if (nodeDragRef.current?.isDragging) {
      nodeDragRef.current = null;
    }
    panRef.current = null;
    nodeDragRef.current = null;
    setHoveredNode(null);
  }, []);

  // Wheel zoom with passive: false
  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault();
    const scaleFactor = e.deltaY > 0 ? 0.92 : 1.08;
    setTransform((prev) => ({
      ...prev,
      scale: Math.max(0.3, Math.min(3, prev.scale * scaleFactor)),
    }));
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.addEventListener("wheel", handleWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  // ── Zoom / reset controls ─────────────────────────────────────────

  const handleZoomIn = useCallback(() => {
    setTransform((prev) => ({
      ...prev,
      scale: Math.min(3, prev.scale * 1.25),
    }));
  }, []);

  const handleZoomOut = useCallback(() => {
    setTransform((prev) => ({
      ...prev,
      scale: Math.max(0.3, prev.scale * 0.8),
    }));
  }, []);

  const handleReset = useCallback(() => {
    setTransform({ x: 0, y: 0, scale: 1 });
    // Unpin all nodes and reheat
    for (const n of graphRef.current.nodes) n.pinned = false;
    if (layoutMode === "force") {
      alphaRef.current = 0.8;
    } else {
      applyLayout(layoutMode);
    }
  }, [layoutMode, applyLayout]);

  // ── Cursor logic ──────────────────────────────────────────────────
  const cursorClass = nodeDragRef.current?.isDragging
    ? "cursor-grabbing"
    : hoveredNode
      ? "cursor-pointer"
      : "cursor-grab active:cursor-grabbing";

  // ── Domain legend ─────────────────────────────────────────────────
  const domains = [...new Set(allSkills.map((s) => s.domain))];

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

      {/* Hover tooltip */}
      {hoveredNode &&
        !nodeDragRef.current?.isDragging &&
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

      {/* Layout mode selector */}
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

      {/* Zoom controls */}
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

      {/* Domain legend */}
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
          {domains.map((d) => (
            <div key={d} className="flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: getDomainColor(d) }}
              />
              <span className="text-foreground capitalize" style={typo.helper}>
                {d}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Skill count + hint */}
      <div
        className="absolute top-4 right-4 px-3 py-1.5 rounded-button border border-border-subtle shadow-sm"
        style={{ backgroundColor: "var(--card)" }}
      >
        <span className="text-muted-foreground" style={typo.helper}>
          {allSkills.length} skills · {graphRef.current.edges.length}{" "}
          connections
        </span>
      </div>
    </div>
  );
}
