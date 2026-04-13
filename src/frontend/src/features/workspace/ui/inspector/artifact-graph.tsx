import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
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
} from "@/features/workspace/ui/inspector/graph-step-node.constants";
import {
  GraphStepNode,
  type GraphStepNodeData,
} from "@/features/workspace/ui/inspector/graph-step-node";
import { extractToolBadgeFromStep } from "@/features/workspace/ui/inspector/graph-tool-badge";
import { summarizeArtifactStep } from "@/features/workspace/ui/inspector/parsers/artifact-payload-summaries";

interface ArtifactGraphProps {
  steps: ExecutionStep[];
  activeStepId?: string;
  onSelectStep: (id: string) => void;
  isVisible?: boolean;
}

const nodeTypes: NodeTypes = { step: GraphStepNode };

const ROW_HEIGHT = 210;
const LANE_WIDTH = NODE_WIDTH + 96;

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

function formatElapsedLabel(ms: number | undefined): string | undefined {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return undefined;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const mins = Math.floor(seconds / 60);
  const rem = Math.round(seconds % 60);
  return `${mins}m ${rem}s`;
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
    const laneIndexByKey = new Map(lanes.map((lane, index) => [lane.key, index]));

    const graphNodes: Node<GraphStepNodeData>[] = [];
    const graphEdges: Edge[] = [];
    const nodeIdByStepId = new Map<string, string>();

    for (let index = 0; index < ordered.length; index += 1) {
      const step = ordered[index]!;
      const lane = deriveLane(step);
      const laneIndex = laneIndexByKey.get(lane.key) ?? 0;
      const toolBadge = extractToolBadgeFromStep(step);
      const nodeId = `node-${step.id}`;

      nodeIdByStepId.set(step.id, nodeId);

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
          y: index * ROW_HEIGHT,
        },
        selected: step.id === activeStepId,
      });
    }

    for (const step of ordered) {
      if (!step.parent_id) continue;
      const source = nodeIdByStepId.get(step.parent_id);
      const target = nodeIdByStepId.get(step.id);
      if (!source || !target || source === target) continue;

      const edgeColor = STEP_TYPE_META[step.type]?.color ?? "var(--border)";
      graphEdges.push({
        id: `parent-${source}-${target}`,
        source,
        target,
        type: "smoothstep",
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
      const elapsedLabel = formatElapsedLabel(current.timestamp - previous.timestamp);
      graphEdges.push({
        id: `chrono-${source}-${target}`,
        source,
        target,
        type: "smoothstep",
        animated: false,
        style: {
          stroke: "var(--trace-edge-secondary)",
          strokeWidth: 0.9,
          strokeDasharray: "4 4",
          opacity: 0.65,
        },
        label: elapsedLabel,
        labelShowBg: true,
        labelBgStyle: {
          fill: "color-mix(in srgb, var(--background) 85%, transparent)",
          opacity: 0.95,
        },
        labelStyle: {
          fontSize: 10,
          fill: "var(--trace-edge-secondary)",
          fontVariantNumeric: "tabular-nums",
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
    <div className="h-full w-full rounded-xl border border-border-subtle/80 overflow-hidden bg-card/30">
      <div ref={containerRef} className="h-full min-h-0">
        {isVisible && containerReady ? (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.2, maxZoom: 1.2 }}
            nodesDraggable={false}
            nodesConnectable={false}
            panOnScroll
            panOnScrollMode={PanOnScrollMode.Vertical}
            selectionOnDrag={false}
            onNodeClick={onNodeClick}
            className="artifact-graph-flow bg-background"
          >
            <Background gap={20} color="var(--border-subtle)" />
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
