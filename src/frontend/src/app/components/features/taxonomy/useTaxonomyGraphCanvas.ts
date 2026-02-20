import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import type { Skill } from "../../data/types";
import {
  type GraphEdge,
  type GraphNode,
  type LayoutMode,
  applyClusterLayout,
  applyTreeLayout,
  simulateForces,
} from "../../data/graph-layouts";
import {
  buildGraph,
  clientToGraph,
  domainColorMap,
  hitTestNode,
  resolveCSSVar,
  withAlpha,
} from "../../../lib/taxonomy/graph";

const DRAG_THRESHOLD = 3;

interface UseTaxonomyGraphCanvasArgs {
  skills: Skill[];
  selectedSkillId: string | null;
  prefersReduced: boolean | null;
  onSelectSkill: (skillId: string) => void;
}

interface CanvasTransform {
  x: number;
  y: number;
  scale: number;
}

export function useTaxonomyGraphCanvas({
  skills,
  selectedSkillId,
  prefersReduced,
  onSelectSkill,
}: UseTaxonomyGraphCanvasArgs) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef(buildGraph(skills));
  const animRef = useRef<number>(0);
  const alphaRef = useRef(1);

  const [layoutMode, setLayoutMode] = useState<LayoutMode>("force");
  const layoutModeRef = useRef(layoutMode);
  layoutModeRef.current = layoutMode;

  const [transform, setTransform] = useState<CanvasTransform>({
    x: 0,
    y: 0,
    scale: 1,
  });
  const transformRef = useRef(transform);
  transformRef.current = transform;

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

    const draggingId = nodeDragRef.current?.isDragging
      ? nodeDragRef.current.node.id
      : null;

    for (const node of nodes) {
      const isSelected = node.id === selectedSkillId;
      const isHovered = hoveredNode?.id === node.id;
      const isDragged = node.id === draggingId;
      const domainColor = colors.domainColors[node.domain] ?? colors.mutedFg;

      if (isSelected || isHovered || isDragged) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.radius + (isDragged ? 6 : 4), 0, Math.PI * 2);
        ctx.fillStyle = isDragged
          ? withAlpha(colors.accent, 0.15)
          : isSelected
            ? withAlpha(colors.accent, 0.2)
            : withAlpha(domainColor, 0.12);
        ctx.fill();
      }

      if (node.pinned && !isDragged) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.radius + 3, 0, Math.PI * 2);
        ctx.strokeStyle = withAlpha(colors.accent, 0.4);
        ctx.lineWidth = 1.5 / t.scale;
        ctx.setLineDash([3 / t.scale, 3 / t.scale]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      ctx.beginPath();
      ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
      ctx.fillStyle = withAlpha(domainColor, isSelected || isDragged ? 1 : 0.85);
      ctx.fill();
      ctx.strokeStyle = isSelected || isDragged ? colors.accent : domainColor;
      ctx.lineWidth = (isSelected || isDragged ? 2.5 : 1.5) / t.scale;
      ctx.stroke();

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
          ctx.measureText(label + "…").width > maxWidth
        ) {
          label = label.slice(0, -1);
        }
        label += "…";
      }
      ctx.fillText(label, node.x, node.y);
    }

    ctx.restore();
  }, [hoveredNode, resolveColors, selectedSkillId]);

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

    const htmlEl = document.documentElement;
    const mutObs = new MutationObserver(() => resolveColors());
    mutObs.observe(htmlEl, { attributes: true, attributeFilter: ["class"] });

    return () => {
      cancelAnimationFrame(animRef.current);
      obs.disconnect();
      mutObs.disconnect();
    };
  }, [draw, prefersReduced, resolveColors]);

  const applyLayout = useCallback(
    (mode: LayoutMode) => {
      setLayoutMode(mode);
      const { nodes, edges } = graphRef.current;

      for (const n of nodes) n.pinned = false;

      if (mode === "cluster") {
        applyClusterLayout(nodes);
      } else if (mode === "tree") {
        applyTreeLayout(nodes, edges);
      } else {
        alphaRef.current = 1;
      }

      if (mode !== "force" && prefersReduced) {
        draw();
      }
    },
    [draw, prefersReduced],
  );

  const hitTest = useCallback((clientX: number, clientY: number): GraphNode | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const { gx, gy } = clientToGraph(clientX, clientY, canvas, transformRef.current);
    return hitTestNode(graphRef.current.nodes, gx, gy);
  }, []);

  const handleMouseDown = useCallback(
    (e: ReactMouseEvent<HTMLCanvasElement>) => {
      const node = hitTest(e.clientX, e.clientY);
      if (node) {
        nodeDragRef.current = {
          node,
          startClientX: e.clientX,
          startClientY: e.clientY,
          isDragging: false,
        };
        return;
      }

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
    (e: ReactMouseEvent<HTMLCanvasElement>) => {
      if (nodeDragRef.current) {
        const nd = nodeDragRef.current;
        const dx = e.clientX - nd.startClientX;
        const dy = e.clientY - nd.startClientY;

        if (!nd.isDragging && Math.sqrt(dx * dx + dy * dy) > DRAG_THRESHOLD) {
          nd.isDragging = true;
          nd.node.pinned = true;
          if (layoutMode === "force") {
            alphaRef.current = Math.max(alphaRef.current, 0.3);
          }
        }

        if (nd.isDragging) {
          const canvas = canvasRef.current;
          if (!canvas) return;
          const { gx, gy } = clientToGraph(
            e.clientX,
            e.clientY,
            canvas,
            transformRef.current,
          );
          nd.node.x = gx;
          nd.node.y = gy;
          nd.node.vx = 0;
          nd.node.vy = 0;
        }
        return;
      }

      if (panRef.current) {
        const dx = (e.clientX - panRef.current.startX) / transformRef.current.scale;
        const dy = (e.clientY - panRef.current.startY) / transformRef.current.scale;
        setTransform((prev) => ({
          ...prev,
          x: panRef.current!.originX + dx,
          y: panRef.current!.originY + dy,
        }));
        return;
      }

      const node = hitTest(e.clientX, e.clientY);
      setHoveredNode(node);
      if (node) {
        setTooltipPos({ x: e.clientX, y: e.clientY });
      }
    },
    [hitTest, layoutMode],
  );

  const handleMouseUp = useCallback(() => {
    if (nodeDragRef.current) {
      const nd = nodeDragRef.current;
      if (!nd.isDragging) {
        onSelectSkill(nd.node.id);
      }
      nodeDragRef.current = null;
      return;
    }
    panRef.current = null;
  }, [onSelectSkill]);

  const handleMouseLeave = useCallback(() => {
    if (nodeDragRef.current?.isDragging) {
      nodeDragRef.current = null;
    }
    panRef.current = null;
    nodeDragRef.current = null;
    setHoveredNode(null);
  }, []);

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
    for (const n of graphRef.current.nodes) n.pinned = false;
    if (layoutMode === "force") {
      alphaRef.current = 0.8;
    } else {
      applyLayout(layoutMode);
    }
  }, [applyLayout, layoutMode]);

  const cursorClass = nodeDragRef.current?.isDragging
    ? "cursor-grabbing"
    : hoveredNode
      ? "cursor-pointer"
      : "cursor-grab active:cursor-grabbing";

  const domains = useMemo(() => [...new Set(skills.map((s) => s.domain))], [skills]);

  return {
    canvasRef,
    containerRef,
    layoutMode,
    applyLayout,
    hoveredNode,
    tooltipPos,
    cursorClass,
    domains,
    edgeCount: graphRef.current.edges.length,
    handleMouseDown,
    handleMouseMove,
    handleMouseUp,
    handleMouseLeave,
    handleZoomIn,
    handleZoomOut,
    handleReset,
  };
}

export type UseTaxonomyGraphCanvasResult = ReturnType<typeof useTaxonomyGraphCanvas>;
export type { LayoutMode, GraphNode, GraphEdge };
