import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  PanOnScrollMode,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { ArtifactActorKind, ExecutionStep } from "@/features/workspace/use-workspace";
import {
  NODE_WIDTH,
  STEP_TYPE_META,
} from "@/features/workspace/inspection/graph-step-node.constants";
import {
  GraphStepNode,
  type GraphStepNodeData,
} from "@/features/workspace/inspection/graph-step-node";
import { extractToolBadgeFromStep } from "@/features/workspace/inspection/graph-tool-badge";
import { summarizeArtifactStep } from "@/features/workspace/inspection/parsers/artifact-payload-summaries";

interface ArtifactGraphProps {
  steps: ExecutionStep[];
  activeStepId?: string;
  onSelectStep: (id: string) => void;
  isVisible?: boolean;
}

const nodeTypes: NodeTypes = { step: GraphStepNode };

const ROW_HEIGHT = 210;
const LANE_WIDTH = NODE_WIDTH + 96;
const MAX_ROWS_PER_LANE_COLUMN = 8;
const FIT_VIEW_NODE_WINDOW = 4;

const ACTOR_PRIORITY: Record<ArtifactActorKind, number> = {
  root_rlm: 0,
  sub_agent: 1,
  delegate: 2,
  unknown: 3,
};

interface LaneMeta {
  key: string;
  label: string;
  actorKind: ArtifactActorKind;
  depth?: number;
}

interface LanePosition {
  laneIndex: number;
  rowIndex: number;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function normalizeActorKind(value: unknown): ArtifactActorKind {
  const raw = String(value ?? "")
    .trim()
    .toLowerCase();
  if (raw === "root_rlm" || raw === "root-rlm" || raw === "root") {
    return "root_rlm";
  }
  if (raw === "sub_agent" || raw === "sub-agent" || raw === "subagent") {
    return "sub_agent";
  }
  if (raw === "delegate" || raw === "rlm_delegate" || raw === "rlm-delegate") {
    return "delegate";
  }
  return "unknown";
}

function normalizeDepth(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.max(0, Math.trunc(value));
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return Math.max(0, Math.trunc(parsed));
  }
  return undefined;
}

function inferStatus(step: ExecutionStep): "streaming" | "complete" | "error" {
  const output = asRecord(step.output);
  const input = asRecord(step.input);
  if (output?.streaming === true) return "streaming";
  if (asRecord(output?.error) || asRecord(input?.error)) return "error";
  if (output?.ok === false || input?.ok === false) return "error";
  if (output?.status === "error" || input?.status === "error") return "error";
  if (typeof output?.message === "string" && /error|failed|exception/i.test(output.message)) {
    return "error";
  }
  if (step.type === "output") {
    const label = step.label.toLowerCase();
    if (label.includes("error")) return "error";
  }
  if (/error|failed|exception/i.test(step.label)) return "error";
  return "complete";
}

function summarizeStep(step: ExecutionStep): string {
  return summarizeArtifactStep(step);
}

function laneLabel(kind: ArtifactActorKind, depth?: number): string {
  if (kind === "root_rlm") return "Root RLM";
  if (kind === "delegate") {
    return typeof depth === "number" ? `Delegate (depth ${depth})` : "Delegate";
  }
  if (kind === "sub_agent") {
    return typeof depth === "number" ? `Sub-agent (depth ${depth})` : "Sub-agent";
  }
  return typeof depth === "number" ? `Unknown (depth ${depth})` : "Unknown";
}

function deriveLane(step: ExecutionStep): LaneMeta {
  let actorKind = normalizeActorKind(step.actor_kind);
  let depth = normalizeDepth(step.depth);
  if (actorKind === "unknown" && depth == null && !step.parent_id) {
    actorKind = "root_rlm";
    depth = 0;
  }
  const actorId =
    typeof step.actor_id === "string" && step.actor_id.trim() ? step.actor_id.trim() : undefined;

  const key =
    (typeof step.lane_key === "string" && step.lane_key.trim()) ||
    (actorId ? `${actorKind}:${actorId}` : `${actorKind}:depth-${depth ?? "na"}`);

  const label = actorId
    ? `${laneLabel(actorKind, depth)} · ${actorId}`
    : laneLabel(actorKind, depth);

  return { key, label, actorKind, depth };
}

function sortStepsChronologically(steps: ExecutionStep[]): ExecutionStep[] {
  return [...steps].sort((a, b) => {
    const aSeq = a.sequence;
    const bSeq = b.sequence;
    if (aSeq != null && bSeq != null && aSeq !== bSeq) return aSeq - bSeq;
    if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp;
    return a.id.localeCompare(b.id);
  });
}

function buildLanes(ordered: ExecutionStep[]): LaneMeta[] {
  const byKey = new Map<string, LaneMeta>();
  for (const step of ordered) {
    const lane = deriveLane(step);
    if (!byKey.has(lane.key)) {
      byKey.set(lane.key, lane);
    }
  }

  return [...byKey.values()].sort((a, b) => {
    if (a.actorKind !== b.actorKind) {
      return ACTOR_PRIORITY[a.actorKind] - ACTOR_PRIORITY[b.actorKind];
    }
    if ((a.depth ?? -1) !== (b.depth ?? -1)) {
      return (a.depth ?? -1) - (b.depth ?? -1);
    }
    return a.label.localeCompare(b.label);
  });
}

function isJsDomEnvironment(): boolean {
  return typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);
}

export function ArtifactGraph({
  steps,
  activeStepId,
  onSelectStep,
  isVisible = true,
}: ArtifactGraphProps) {
  const [expandedNodeId, setExpandedNodeId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [containerReady, setContainerReady] = useState(
    () => isVisible && (typeof ResizeObserver === "undefined" || isJsDomEnvironment()),
  );

  const { nodes, edges } = useMemo(() => {
    const ordered = sortStepsChronologically(steps);
    const lanes = buildLanes(ordered);
    const laneBaseIndexByKey = new Map(lanes.map((lane, index) => [lane.key, index]));
    const laneCountsByKey = new Map<string, number>();
    const extraColumnsBeforeLane = new Map<string, number>();
    let extraColumnCount = 0;

    for (const lane of lanes) {
      extraColumnsBeforeLane.set(lane.key, extraColumnCount);
      const laneCount = ordered.filter((step) => deriveLane(step).key === lane.key).length;
      const extraForLane = Math.max(0, Math.ceil(laneCount / MAX_ROWS_PER_LANE_COLUMN) - 1);
      extraColumnCount += extraForLane;
    }

    const graphNodes: Node<GraphStepNodeData>[] = [];
    const graphEdges: Edge[] = [];
    const nodeIdByStepId = new Map<string, string>();
    const nodePositionByStepId = new Map<string, LanePosition>();

    for (let index = 0; index < ordered.length; index += 1) {
      const step = ordered[index]!;
      const lane = deriveLane(step);
      const stepLaneIndex = laneCountsByKey.get(lane.key) ?? 0;
      laneCountsByKey.set(lane.key, stepLaneIndex + 1);
      const wrappedColumnIndex = Math.floor(stepLaneIndex / MAX_ROWS_PER_LANE_COLUMN);
      const rowIndex = stepLaneIndex % MAX_ROWS_PER_LANE_COLUMN;
      const laneIndex =
        (laneBaseIndexByKey.get(lane.key) ?? 0) +
        (extraColumnsBeforeLane.get(lane.key) ?? 0) +
        wrappedColumnIndex;
      const toolBadge = extractToolBadgeFromStep(step);
      const nodeId = `node-${step.id}`;

      nodeIdByStepId.set(step.id, nodeId);
      nodePositionByStepId.set(step.id, { laneIndex, rowIndex });

      graphNodes.push({
        id: nodeId,
        type: "step",
        data: {
          label: step.label,
          type: step.type,
          actorKind: lane.actorKind,
          actorId: typeof step.actor_id === "string" ? step.actor_id : null,
          depth: normalizeDepth(step.depth) ?? null,
          laneLabel: lane.label,
          summary: summarizeStep(step),
          count: 1,
          representativeStepId: step.id,
          toolName: toolBadge.toolName,
          toolNameSource: toolBadge.toolNameSource,
          status: inferStatus(step),
          expanded: step.id === expandedNodeId,
          input: step.input,
          output: step.output,
        },
        position: {
          x: laneIndex * LANE_WIDTH,
          y: rowIndex * ROW_HEIGHT,
        },
        selected: step.id === activeStepId,
      });
    }

    for (const step of ordered) {
      if (!step.parent_id) continue;
      const source = nodeIdByStepId.get(step.parent_id);
      const target = nodeIdByStepId.get(step.id);
      if (!source || !target || source === target) continue;
      const sourcePosition = nodePositionByStepId.get(step.parent_id);
      const targetPosition = nodePositionByStepId.get(step.id);
      const edgeType =
        sourcePosition && targetPosition && sourcePosition.laneIndex === targetPosition.laneIndex
          ? "smoothstep"
          : "default";

      const edgeColor = STEP_TYPE_META[step.type]?.color ?? "var(--border)";
      graphEdges.push({
        id: `parent-${source}-${target}`,
        source,
        target,
        type: edgeType,
        animated: step.id === activeStepId,
        style: { stroke: edgeColor, strokeWidth: 1.35, opacity: 0.86 },
      });
    }

    for (let index = 1; index < ordered.length; index += 1) {
      const previous = ordered[index - 1]!;
      const current = ordered[index]!;
      const source = nodeIdByStepId.get(previous.id);
      const target = nodeIdByStepId.get(current.id);
      if (!source || !target || source === target) continue;
      const sourcePosition = nodePositionByStepId.get(previous.id);
      const targetPosition = nodePositionByStepId.get(current.id);
      const edgeType =
        sourcePosition && targetPosition && sourcePosition.laneIndex === targetPosition.laneIndex
          ? "smoothstep"
          : "default";
      graphEdges.push({
        id: `chrono-${source}-${target}`,
        source,
        target,
        type: edgeType,
        animated: false,
        className: "artifact-graph-edge-chrono",
        style: {
          stroke: "var(--trace-edge-secondary)",
          strokeWidth: 0.9,
          strokeDasharray: "4 4",
          opacity: 0.65,
        },
      });
    }

    return { nodes: graphNodes, edges: graphEdges };
  }, [activeStepId, expandedNodeId, steps]);

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_event, node) => {
      const graphNode = node as Node<GraphStepNodeData>;
      const stepId = graphNode.data.representativeStepId;
      onSelectStep(stepId);
      setExpandedNodeId((prev) => (prev === stepId ? null : stepId));
    },
    [onSelectStep],
  );

  const fitViewNodes = useMemo(() => {
    if (nodes.length <= FIT_VIEW_NODE_WINDOW) return undefined;
    const activeIndex = Math.max(
      0,
      nodes.findIndex((node) => node.data.representativeStepId === activeStepId),
    );
    const activeNode = nodes[activeIndex];
    const columnNodes = activeNode
      ? nodes.filter((node) => node.position.x === activeNode.position.x)
      : nodes;
    const activeColumnIndex = Math.max(
      0,
      columnNodes.findIndex((node) => node.id === activeNode?.id),
    );
    const startIndex = Math.max(
      0,
      Math.min(activeColumnIndex - 1, columnNodes.length - FIT_VIEW_NODE_WINDOW),
    );
    return columnNodes
      .slice(startIndex, startIndex + FIT_VIEW_NODE_WINDOW)
      .map((node) => ({ id: node.id }));
  }, [activeStepId, nodes]);

  useEffect(() => {
    if (!isVisible) {
      setContainerReady(false);
      return;
    }

    const element = containerRef.current;
    if (!element) return;

    const markReadyIfSized = () => {
      const rect = element.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setContainerReady(true);
        return true;
      }
      return false;
    };

    if (markReadyIfSized()) return;

    const frameId = window.requestAnimationFrame(() => {
      markReadyIfSized();
    });

    if (typeof ResizeObserver === "undefined") {
      return () => {
        window.cancelAnimationFrame(frameId);
      };
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      if (entry.contentRect.width > 0 && entry.contentRect.height > 0) {
        setContainerReady(true);
      }
    });

    observer.observe(element);
    return () => {
      window.cancelAnimationFrame(frameId);
      observer.disconnect();
    };
  }, [isVisible, steps.length]);

  if (steps.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
        Graph appears once the run starts.
      </div>
    );
  }

  return (
    <div className="h-full min-w-0 w-full max-w-full overflow-hidden rounded-xl border border-border-subtle/80 bg-card/30">
      <div ref={containerRef} className="h-full min-h-0 min-w-0 w-full max-w-full overflow-hidden">
        {isVisible && containerReady ? (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.35, maxZoom: 1.05, nodes: fitViewNodes }}
            nodesDraggable={false}
            nodesConnectable={false}
            panOnScroll
            panOnScrollMode={PanOnScrollMode.Vertical}
            selectionOnDrag={false}
            onNodeClick={onNodeClick}
            className="artifact-graph-flow bg-background"
            style={{ width: "100%", height: "100%" }}
          >
            <Background gap={20} color="var(--border-subtle)" />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        ) : isVisible ? (
          <div className="flex h-full min-h-0 items-center justify-center text-sm text-muted-foreground">
            Preparing graph…
          </div>
        ) : null}
      </div>
    </div>
  );
}
