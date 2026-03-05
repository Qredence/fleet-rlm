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

import type {
  ArtifactActorKind,
  ArtifactStepType,
  ExecutionStep,
} from "@/stores/artifactStore";
import {
  NODE_WIDTH,
  STEP_TYPE_META,
} from "@/features/artifacts/components/GraphStepNode.constants";
import {
  GraphStepNode,
  type GraphStepNodeData,
} from "@/features/artifacts/components/GraphStepNode";
import { extractToolBadgeFromStep } from "@/features/artifacts/components/graphToolBadge";
import { summarizeArtifactStep } from "@/features/artifacts/parsers/artifactPayloadSummaries";

interface ArtifactGraphProps {
  steps: ExecutionStep[];
  activeStepId?: string;
  onSelectStep: (id: string) => void;
}

const nodeTypes: NodeTypes = { step: GraphStepNode };

const ROW_HEIGHT = 230;
const LANE_WIDTH = NODE_WIDTH + 120;

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
  if (
    typeof output?.message === "string" &&
    /error|failed|exception/i.test(output.message)
  ) {
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
    return typeof depth === "number"
      ? `Sub-agent (depth ${depth})`
      : "Sub-agent";
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
    typeof step.actor_id === "string" && step.actor_id.trim()
      ? step.actor_id.trim()
      : undefined;

  const key =
    (typeof step.lane_key === "string" && step.lane_key.trim()) ||
    (actorId
      ? `${actorKind}:${actorId}`
      : `${actorKind}:depth-${depth ?? "na"}`);

  const label = actorId
    ? `${laneLabel(actorKind, depth)} · ${actorId}`
    : laneLabel(actorKind, depth);

  return { key, label, actorKind, depth };
}

function sortStepsChronologically(steps: ExecutionStep[]): ExecutionStep[] {
  return [...steps].sort((a, b) => {
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
            <Icon
              className="size-3"
              style={{ color: meta.color }}
              aria-hidden
            />
            <span>{meta.label}</span>
          </div>
        );
      })}
    </div>
  );
}

function LaneLegend({ lanes }: { lanes: LaneMeta[] }) {
  if (lanes.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 px-3 py-2 text-[11px] text-muted-foreground border-b border-border-subtle bg-card/40">
      {lanes.map((lane) => (
        <span
          key={lane.key}
          className="inline-flex items-center rounded-full border border-border-subtle bg-muted/40 px-2 py-0.5 text-[11px] text-foreground/90"
        >
          {lane.label}
        </span>
      ))}
    </div>
  );
}

export function ArtifactGraph({
  steps,
  activeStepId,
  onSelectStep,
}: ArtifactGraphProps) {
  const [expandedNodeId, setExpandedNodeId] = useState<string | null>(null);

  const { nodes, edges, lanes } = useMemo(() => {
    const ordered = sortStepsChronologically(steps);
    const lanes = buildLanes(ordered);
    const laneIndexByKey = new Map(
      lanes.map((lane, index) => [lane.key, index]),
    );

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
        style: { stroke: edgeColor, strokeWidth: 1.8 },
      });
    }

    for (let index = 1; index < ordered.length; index += 1) {
      const previous = ordered[index - 1]!;
      const current = ordered[index]!;
      const source = nodeIdByStepId.get(previous.id);
      const target = nodeIdByStepId.get(current.id);
      if (!source || !target || source === target) continue;

      const elapsedLabel = formatElapsedLabel(
        current.timestamp - previous.timestamp,
      );
      graphEdges.push({
        id: `chrono-${source}-${target}`,
        source,
        target,
        type: "smoothstep",
        animated: false,
        style: {
          stroke: "var(--muted-foreground)",
          strokeWidth: 1,
          strokeDasharray: "4 4",
          opacity: 0.7,
        },
        label: elapsedLabel,
        labelShowBg: true,
        labelBgStyle: {
          fill: "color-mix(in srgb, var(--background) 85%, transparent)",
          opacity: 0.95,
        },
        labelStyle: {
          fontSize: 10,
          fill: "var(--muted-foreground)",
          fontVariantNumeric: "tabular-nums",
        },
      });
    }

    return { nodes: graphNodes, edges: graphEdges, lanes };
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
      <LaneLegend lanes={lanes} />
      <div className="flex-1 min-h-0">
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
