import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { CircleDot, Clock, GitBranch, TriangleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { EmptyPanel } from "@/components/product/empty-panel";
import { DetailBlock } from "@/features/workspace/inspection/inspector-ui";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import {
  buildTraceFlowGraph,
  type TraceSpanNodeData,
} from "@/features/workspace/screen/workspace-session-trace-model";
import { type SessionTraceDebugResponse } from "@/lib/rlm-api/sessions";
import { useChatStore, type ExecutionStep } from "@/features/workspace/use-workspace";
import {
  GraphInspectorContent,
  hasMeaningfulGraph,
} from "@/features/workspace/inspection/tabs/graph-inspector-content";
import { cn } from "@/lib/utils";

import {
  type SessionTraceState,
  useSelectedWorkspaceTurn,
  traceStatusTone,
} from "../use-session-trace";
import {
  LiveTurnTrajectoryFallback,
  TraceErrorPanel,
  TraceLoading,
  selectedTurnHasTimeline,
} from "./trajectory-tab";

const traceNodeTypes: NodeTypes = {
  traceSpan: memo(function TraceSpanNode({ data, selected }: NodeProps<Node<TraceSpanNodeData>>) {
    const tone = traceStatusTone(data.status);
    const statusClass =
      data.status === "failed"
        ? "bg-destructive"
        : data.status === "running"
          ? "bg-accent animate-pulse"
          : data.status === "completed"
            ? "bg-chart-2"
            : "bg-muted-foreground";

    return (
      <div
        className={cn(
          "relative w-64 rounded-lg border bg-card/80 px-3 py-2 text-foreground shadow-sm transition-colors",
          selected ? "border-accent ring-1 ring-accent/70" : "border-border-subtle/80",
        )}
      >
        <Handle type="target" position={Position.Left} className="size-2 bg-border!" />
        <Handle type="source" position={Position.Right} className="size-2 bg-border!" />
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate typo-caption font-semibold">{data.label}</div>
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <Badge variant="outline" className="rounded-full typo-micro">
                {data.kind}
              </Badge>
              {data.toolName ? (
                <Badge variant="secondary" className="max-w-full rounded-full typo-micro">
                  <span className="truncate">{data.toolName}</span>
                </Badge>
              ) : null}
            </div>
          </div>
          <span className={cn("mt-1 size-2 shrink-0 rounded-full", statusClass)} />
        </div>
        {data.summary ? (
          <p className="mt-2 line-clamp-3 text-muted-foreground typo-helper">{data.summary}</p>
        ) : null}
        <div className="mt-2 flex items-center gap-2 text-muted-foreground typo-micro">
          <CircleDot className="size-3" />
          <span>{tone.label}</span>
          {data.durationLabel ? (
            <>
              <Clock className="size-3" />
              <span>{data.durationLabel}</span>
            </>
          ) : null}
          {data.tokenLabel ? <span>{data.tokenLabel}</span> : null}
        </div>
        {data.fallbackReason ? (
          <Badge variant="destructive" className="mt-2 rounded-full typo-micro">
            {data.fallbackReason}
          </Badge>
        ) : null}
      </div>
    );
  }),
};

function SelectedTraceNodeDetail({ node }: { node: Node<TraceSpanNodeData> | undefined }) {
  if (!node) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground typo-caption">
        Select a span node to inspect its input and output.
      </div>
    );
  }

  const span = node.data.span;
  return (
    <ScrollArea className="h-full">
      <div className="space-y-3 p-3">
        <div>
          <div className="typo-label font-medium text-foreground">{node.data.label}</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <Badge variant="outline" className={inspectorStyles.badge.meta}>
              {node.data.kind}
            </Badge>
            <Badge
              variant={traceStatusTone(node.data.status).variant}
              className={inspectorStyles.badge.meta}
            >
              {traceStatusTone(node.data.status).label}
            </Badge>
            {node.data.componentType ? (
              <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                {node.data.componentType}
              </Badge>
            ) : null}
            {node.data.durationLabel ? (
              <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                {node.data.durationLabel}
              </Badge>
            ) : null}
            {node.data.tokenLabel ? (
              <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                {node.data.tokenLabel}
              </Badge>
            ) : null}
            {node.data.outputSizeLabel ? (
              <Badge variant="outline" className={inspectorStyles.badge.meta}>
                output {node.data.outputSizeLabel}
              </Badge>
            ) : null}
            {node.data.fallbackReason ? (
              <Badge variant="destructive" className={inspectorStyles.badge.meta}>
                {node.data.fallbackReason}
              </Badge>
            ) : null}
          </div>
        </div>
        <DetailBlock label="Rationale" value={span.rationale ?? undefined} />
        <DetailBlock
          label="Metrics"
          value={[
            node.data.durationLabel ? `Duration: ${node.data.durationLabel}` : null,
            node.data.tokenLabel ? `Tokens: ${node.data.tokenLabel}` : null,
            node.data.outputSizeLabel ? `Output: ${node.data.outputSizeLabel}` : null,
            node.data.fallbackReason ? `Fallback: ${node.data.fallbackReason}` : null,
          ]
            .filter(Boolean)
            .join("\n")}
        />
        <DetailBlock label="Input" value={span.input_preview ?? undefined} />
        <DetailBlock
          label="Output"
          value={span.output_preview ?? undefined}
          tone={node.data.status === "failed" ? "error" : "default"}
        />
      </div>
    </ScrollArea>
  );
}

function TraceFlowGraph({ traceDebug }: { traceDebug: SessionTraceDebugResponse }) {
  const { nodes, edges } = useMemo(() => buildTraceFlowGraph(traceDebug.spans), [traceDebug.spans]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(nodes[0]?.id ?? null);

  useEffect(() => {
    if (selectedNodeId && nodes.some((node) => node.id === selectedNodeId)) return;
    setSelectedNodeId(nodes[0]?.id ?? null);
  }, [nodes, selectedNodeId]);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId),
    [nodes, selectedNodeId],
  );

  const graphNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        selected: node.id === selectedNodeId,
      })),
    [nodes, selectedNodeId],
  );

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_event, node) => setSelectedNodeId(node.id),
    [],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 border-b border-border-subtle/70">
        <ReactFlow
          nodes={graphNodes}
          edges={edges}
          nodeTypes={traceNodeTypes}
          onNodeClick={onNodeClick}
          nodesDraggable={false}
          nodesConnectable={false}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          className="workspace-trace-flow bg-background"
        >
          <Background />
          <Controls />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
      <div className="h-48 shrink-0 overflow-hidden">
        <SelectedTraceNodeDetail node={selectedNode} />
      </div>
    </div>
  );
}

export function GraphTab({ traceState }: { traceState: SessionTraceState }) {
  const selectedTurn = useSelectedWorkspaceTurn();
  const turnArtifactsByMessageId = useChatStore((state) => state.turnArtifactsByMessageId);
  const graphSteps = useMemo<ExecutionStep[]>(
    () => (selectedTurn ? (turnArtifactsByMessageId[selectedTurn.turnId] ?? []) : []),
    [selectedTurn, turnArtifactsByMessageId],
  );
  const fallbackGraph = useMemo(() => {
    if (graphSteps.length === 0) return null;
    if (!hasMeaningfulGraph(graphSteps)) {
      return (
        <EmptyPanel
          title="Linear trajectory"
          description="The live turn emitted artifact steps, but no branches or delegated graph relationships."
          icon={GitBranch}
          className="h-full"
        >
          <Badge variant="secondary" className="rounded-full">
            {graphSteps.length} steps
          </Badge>
        </EmptyPanel>
      );
    }
    return (
      <div className="h-full w-full max-w-full overflow-y-auto overflow-x-hidden">
        <div className="min-w-0 max-w-full overflow-hidden p-3">
          <Alert className="mb-3">
            <TriangleAlert className="text-muted-foreground" />
            <AlertTitle className="typo-label">Trace graph unavailable</AlertTitle>
            <AlertDescription className="typo-caption">
              Rendering the live artifact graph for the latest assistant turn.
            </AlertDescription>
          </Alert>
          <GraphInspectorContent steps={graphSteps} />
        </div>
      </div>
    );
  }, [graphSteps]);

  if (!traceState.traceSessionId) {
    return (
      <EmptyPanel
        title="No active session"
        description="Start or open a workspace session to inspect trace span relationships."
        icon={GitBranch}
        className="h-full"
      />
    );
  }

  if (!traceState.hasSessionContent) {
    return (
      <EmptyPanel
        title="No graph events"
        description="Send a message or open a saved workspace conversation to visualize trace relationships."
        icon={GitBranch}
        className="h-full"
      />
    );
  }

  if (traceState.traceDebugQuery.isLoading || traceState.traceDebugQuery.isFetching) {
    return <TraceLoading label="Loading graph spans..." />;
  }

  if (traceState.traceDebugQuery.isError) {
    if (fallbackGraph) return fallbackGraph;
    if (selectedTurn && selectedTurnHasTimeline(selectedTurn)) {
      return <LiveTurnTrajectoryFallback selectedTurn={selectedTurn} />;
    }
    return (
      <TraceErrorPanel title="Trace graph unavailable" error={traceState.traceDebugQuery.error} />
    );
  }

  const traceDebug = traceState.traceDebugQuery.data;
  if (!traceDebug || traceDebug.spans.length === 0) {
    if (fallbackGraph) return fallbackGraph;
    if (selectedTurn && selectedTurnHasTimeline(selectedTurn)) {
      return <LiveTurnTrajectoryFallback selectedTurn={selectedTurn} />;
    }
    return (
      <EmptyPanel
        title="No graph spans"
        description="The selected session trace exists, but it has no spans to visualize."
        icon={GitBranch}
        className="h-full"
      />
    );
  }

  return <TraceFlowGraph traceDebug={traceDebug} />;
}
