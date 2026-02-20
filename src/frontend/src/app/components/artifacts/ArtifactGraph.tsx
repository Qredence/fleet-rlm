import { useCallback, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  PanOnScrollMode,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
} from "reactflow";
import "reactflow/dist/style.css";
import dagre from "@dagrejs/dagre";

import type {
  ArtifactStepType,
  ExecutionStep,
} from "../../stores/artifactStore";
import {
  GraphStepNode,
  NODE_WIDTH,
  STEP_TYPE_META,
  type GraphStepNodeData,
} from "./GraphStepNode";

// ── Props ───────────────────────────────────────────────────────────

interface ArtifactGraphProps {
  steps: ExecutionStep[];
  activeStepId?: string;
  onSelectStep: (id: string) => void;
}

// ── Custom node type registry (stable ref) ──────────────────────────

const nodeTypes: NodeTypes = { step: GraphStepNode };

// ── Layout constants ────────────────────────────────────────────────

const NODE_HEIGHT = 90;
const DAGRE_NODE_SEP = 16;
const DAGRE_RANK_SEP = 50;

// ── Helpers ─────────────────────────────────────────────────────────

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return undefined;
  return value as Record<string, unknown>;
}

function summarizeStep(step: ExecutionStep): string {
  const text = asText(step.output ?? step.input)
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return step.label;
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}

function normalizeLabel(label: string): string {
  return label.trim().toLowerCase().replace(/\s+/g, " ");
}

function inferStatus(
  step: ExecutionStep,
): "streaming" | "complete" | "error" {
  const output = asRecord(step.output);
  if (output?.streaming === true) return "streaming";
  if (step.type === "output") {
    const label = step.label.toLowerCase();
    if (label.includes("error")) return "error";
  }
  return "complete";
}

// ── Depth index ─────────────────────────────────────────────────────

function buildDepthIndex(steps: ExecutionStep[]): Map<string, number> {
  const byId = new Map(steps.map((step) => [step.id, step]));
  const depthById = new Map<string, number>();

  const walk = (step: ExecutionStep): number => {
    const cached = depthById.get(step.id);
    if (cached != null) return cached;

    const parent = step.parent_id ? byId.get(step.parent_id) : undefined;
    const depth = parent ? walk(parent) + 1 : 0;
    depthById.set(step.id, depth);
    return depth;
  };

  for (const step of steps) {
    walk(step);
  }

  return depthById;
}

// ── Display groups ──────────────────────────────────────────────────

interface DisplayGroup {
  id: string;
  type: ArtifactStepType;
  baseLabel: string;
  summary: string;
  count: number;
  depth: number;
  timestamp: number;
  representativeStepId: string;
  parentStepId?: string;
  normalizedLabel: string;
  status: "streaming" | "complete" | "error";
  elapsedMs?: number;
}

function buildDisplayGroups(ordered: ExecutionStep[]): {
  groups: DisplayGroup[];
  stepToGroupId: Map<string, string>;
} {
  const depthById = buildDepthIndex(ordered);
  const groups: DisplayGroup[] = [];
  const stepToGroupId = new Map<string, string>();

  let currentToolGroup: DisplayGroup | null = null;

  const closeToolGroup = () => {
    if (!currentToolGroup) return;
    groups.push(currentToolGroup);
    currentToolGroup = null;
  };

  const createGroupFromStep = (step: ExecutionStep): DisplayGroup => ({
    id: `group-${step.id}`,
    type: step.type,
    baseLabel: step.label,
    summary: summarizeStep(step),
    count: 1,
    depth: depthById.get(step.id) ?? 0,
    timestamp: step.timestamp,
    representativeStepId: step.id,
    parentStepId: step.parent_id,
    normalizedLabel: normalizeLabel(step.label),
    status: inferStatus(step),
  });

  for (const step of ordered) {
    if (step.type !== "tool") {
      closeToolGroup();
      const group = createGroupFromStep(step);
      groups.push(group);
      stepToGroupId.set(step.id, group.id);
      continue;
    }

    const nl = normalizeLabel(step.label);
    const parentStepId = step.parent_id;

    if (
      currentToolGroup &&
      currentToolGroup.type === "tool" &&
      currentToolGroup.parentStepId === parentStepId &&
      currentToolGroup.normalizedLabel === nl
    ) {
      currentToolGroup.count += 1;
      currentToolGroup.summary = summarizeStep(step);
      currentToolGroup.timestamp = step.timestamp;
      currentToolGroup.representativeStepId = step.id;
      currentToolGroup.status = inferStatus(step);
      stepToGroupId.set(step.id, currentToolGroup.id);
      continue;
    }

    closeToolGroup();
    currentToolGroup = createGroupFromStep(step);
    stepToGroupId.set(step.id, currentToolGroup.id);
  }

  closeToolGroup();

  // Compute elapsed time (delta to next sibling)
  for (let i = 0; i < groups.length; i++) {
    const current = groups[i]!;
    const next = groups[i + 1];
    if (next) {
      current.elapsedMs = next.timestamp - current.timestamp;
    }
  }

  return { groups, stepToGroupId };
}

// ── Dagre auto-layout ───────────────────────────────────────────────

function applyDagreLayout(
  nodes: Node<GraphStepNodeData>[],
  edges: Edge[],
): Node<GraphStepNodeData>[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: "TB",
    nodesep: DAGRE_NODE_SEP,
    ranksep: DAGRE_RANK_SEP,
    marginx: 20,
    marginy: 20,
  });

  for (const node of nodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }

  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });
}

// ── Legend ───────────────────────────────────────────────────────────

const LEGEND_TYPES: ArtifactStepType[] = [
  "llm",
  "repl",
  "tool",
  "memory",
  "output",
];

function GraphLegend() {
  return (
    <div className="flex items-center gap-4 px-3 py-1.5 text-[11px] text-muted-foreground border-b border-border-subtle bg-card/60">
      {LEGEND_TYPES.map((type) => {
        const meta = STEP_TYPE_META[type];
        const Icon = meta.Icon;
        return (
          <div key={type} className="flex items-center gap-1.5">
            <Icon className="size-3" style={{ color: meta.color }} aria-hidden />
            <span>{meta.label}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────

export function ArtifactGraph({
  steps,
  activeStepId,
  onSelectStep,
}: ArtifactGraphProps) {
  const [expandedNodeId, setExpandedNodeId] = useState<string | null>(null);

  const { nodes, edges } = useMemo(() => {
    const ordered = [...steps].sort((a, b) => a.timestamp - b.timestamp);
    const { groups, stepToGroupId } = buildDisplayGroups(ordered);
    const activeGroupId = activeStepId
      ? stepToGroupId.get(activeStepId)
      : undefined;

    const graphNodes: Node<GraphStepNodeData>[] = [];
    const graphEdges: Edge[] = [];
    const seenEdges = new Set<string>();

    for (const group of groups) {
      const isActive = group.id === activeGroupId;
      const label =
        group.count > 1
          ? `${group.baseLabel} ×${group.count}`
          : group.baseLabel;
      const summary =
        group.count > 1
          ? `${group.count} contiguous steps. Latest: ${group.summary}`
          : group.summary;

      graphNodes.push({
        id: group.id,
        type: "step",
        data: {
          label,
          type: group.type,
          summary,
          count: group.count,
          representativeStepId: group.representativeStepId,
          elapsedMs: group.elapsedMs,
          status: group.status,
          expanded: group.id === expandedNodeId,
        },
        position: { x: 0, y: 0 },
        selected: isActive,
      });

      if (!group.parentStepId) continue;
      const sourceGroupId = stepToGroupId.get(group.parentStepId);
      if (!sourceGroupId || sourceGroupId === group.id) continue;

      const edgeId = `${sourceGroupId}-${group.id}`;
      if (seenEdges.has(edgeId)) continue;
      seenEdges.add(edgeId);

      const edgeColor =
        STEP_TYPE_META[group.type]?.color ?? "var(--border)";

      graphEdges.push({
        id: edgeId,
        source: sourceGroupId,
        target: group.id,
        type: "smoothstep",
        animated: group.id === activeGroupId,
        style: { stroke: edgeColor, strokeWidth: 1.5 },
      });
    }

    // Always chain ALL groups sequentially to force a single vertical
    // column layout.  Parent→child edges above are kept as secondary
    // visual hints but are NOT fed into Dagre so siblings don't spread
    // horizontally.
    const layoutEdges: Edge[] = [];
    if (graphNodes.length > 1) {
      for (let i = 0; i < graphNodes.length - 1; i++) {
        const src = graphNodes[i]!;
        const tgt = graphNodes[i + 1]!;
        layoutEdges.push({
          id: `seq-${src.id}-${tgt.id}`,
          source: src.id,
          target: tgt.id,
          type: "smoothstep",
          animated: tgt.selected === true,
          style: { stroke: "var(--border)", strokeWidth: 1 },
        });
      }
    }

    // Use only sequential edges for Dagre layout (vertical column),
    // then expose both sequential + parent→child edges for rendering.
    const layoutNodes = applyDagreLayout(graphNodes, layoutEdges);
    const allEdges = [...layoutEdges, ...graphEdges];
    return { nodes: layoutNodes, edges: allEdges };
  }, [activeStepId, expandedNodeId, steps]);

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_event, node) => {
      const graphNode = node as Node<GraphStepNodeData>;
      onSelectStep(graphNode.data.representativeStepId || graphNode.id);
      setExpandedNodeId((prev) => (prev === graphNode.id ? null : graphNode.id));
    },
    [onSelectStep],
  );

  if (steps.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
        Run a backend prompt to populate the execution graph.
      </div>
    );
  }

  return (
    <div className="h-full w-full rounded-card border border-border-subtle overflow-hidden flex flex-col">
      <GraphLegend />
      <div className="flex-1 min-h-0">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.25, maxZoom: 1.2 }}
          nodesDraggable={false}
          nodesConnectable={false}
          panOnScroll
          panOnScrollMode={PanOnScrollMode.Vertical}
          selectionOnDrag={false}
          onNodeClick={onNodeClick}
          className="bg-background"
        >
          <MiniMap
            pannable
            zoomable
            nodeStrokeWidth={2}
            nodeColor={(node) => {
              const type =
                (node.data as GraphStepNodeData | undefined)?.type ?? "llm";
              return STEP_TYPE_META[type]?.color ?? "var(--muted-foreground)";
            }}
          />
          <Controls />
          <Background gap={20} color="var(--border-subtle)" />
        </ReactFlow>
      </div>
    </div>
  );
}
