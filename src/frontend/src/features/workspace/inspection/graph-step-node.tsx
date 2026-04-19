import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { ArtifactActorKind, ArtifactStepType } from "@/features/workspace/use-workspace";
import { cn } from "@/lib/utils";
import { NodeBadge } from "@/components/product/node-badge";
import {
  NODE_WIDTH,
  STEP_TYPE_META,
} from "@/features/workspace/inspection/graph-step-node.constants";
import {
  extractErrorDetails,
  extractReplCodePreview,
  extractTrajectoryChain,
} from "@/features/workspace/inspection/graph-node-detail-parsers";

// ── Node data shape ─────────────────────────────────────────────────

export interface GraphStepNodeData extends Record<string, unknown> {
  label: string;
  type: ArtifactStepType;
  actorKind?: ArtifactActorKind | null;
  actorId?: string | null;
  depth?: number | null;
  laneLabel?: string;
  summary: string;
  count: number;
  representativeStepId: string;
  toolName?: string;
  toolNameSource?: "payload" | "label";
  elapsedMs?: number;
  status: "streaming" | "complete" | "error";
  expanded?: boolean;
  input?: unknown;
  output?: unknown;
}

function formatActorLabel(actorKind: ArtifactActorKind | null | undefined): string {
  if (actorKind === "delegate") return "Delegate";
  if (actorKind === "sub_agent") return "Sub-agent";
  if (actorKind === "root_rlm") return "Root RLM";
  return "Unknown";
}

// ── Helpers ─────────────────────────────────────────────────────────

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  return seconds < 60
    ? `${seconds.toFixed(1)}s`
    : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

// ── Component ───────────────────────────────────────────────────────

const GraphStepNode = memo(function GraphStepNode({
  data,
  selected,
}: NodeProps<Node<GraphStepNodeData>>) {
  const meta = STEP_TYPE_META[data.type];
  const Icon = meta.Icon;
  const isExpanded = data.expanded === true;
  const codePreview = extractReplCodePreview(data);
  const errorDetails = extractErrorDetails(data);
  const trajectory = extractTrajectoryChain(data);

  const statusDot =
    data.status === "streaming"
      ? "bg-accent animate-pulse"
      : data.status === "error"
        ? "bg-destructive"
        : "bg-muted-foreground";

  return (
    <div
      className={cn(
        "group relative rounded-lg border bg-card/70 text-foreground transition-colors node-border-left",
        selected
          ? "ring-1 ring-accent/70 border-accent/70"
          : "border-border-subtle/80 hover:border-border-strong",
      )}
      style={{ width: NODE_WIDTH, "--node-color": meta.color } as React.CSSProperties}
    >
      <Handle type="target" position={Position.Top} className="bg-border! w-1.5! h-1.5!" />

      <div className="flex items-start gap-2 px-2.5 py-2.5">
        {/* Icon */}
        <div className="mt-0.5 shrink-0 rounded-md p-1 node-tint">
          <Icon className="size-3.5 node-color-text" aria-hidden />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Header row */}
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="typo-caption font-semibold whitespace-pre-wrap wrap-break-word">
              {data.label}
            </span>
            {data.count > 1 && (
              <NodeBadge className="node-tint-strong node-color-text shrink-0">
                ×{data.count}
              </NodeBadge>
            )}
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <NodeBadge>{formatActorLabel(data.actorKind)}</NodeBadge>
            {typeof data.depth === "number" && (
              <NodeBadge>Depth {data.depth}</NodeBadge>
            )}
            {data.actorId && (
              <NodeBadge className="whitespace-pre-wrap wrap-break-word">
                {data.actorId}
              </NodeBadge>
            )}
          </div>

          {data.toolName && (data.type === "tool" || data.type === "repl") && (
            <div className="mt-1 min-w-0">
              <NodeBadge title={data.toolName} className="max-w-full">
                <span className="whitespace-pre-wrap wrap-break-word">{data.toolName}</span>
              </NodeBadge>
            </div>
          )}

          {/* Summary */}
          {data.summary && (
            <p className="mt-0.5 line-clamp-4 typo-helper leading-snug text-muted-foreground whitespace-pre-wrap wrap-break-word">
              {data.summary}
            </p>
          )}

          {/* Footer badges */}
          <div className="mt-1.5 flex items-center gap-1.5">
            {/* Status dot */}
            <span
              className={cn("size-1.25 rounded-full shrink-0", statusDot)}
              aria-label={data.status}
            />
            <span className="typo-micro text-muted-foreground capitalize">{data.status}</span>

            {/* Elapsed time */}
            {data.elapsedMs != null && data.elapsedMs > 0 && (
              <span className="typo-micro text-muted-foreground tabular-nums">
                {formatElapsed(data.elapsedMs)}
              </span>
            )}
          </div>
        </div>
      </div>

      {!isExpanded && codePreview && (
        <div className="pointer-events-none absolute left-full top-0 z-50 ml-2 w-72 max-w-sm rounded-lg border border-border-subtle/80 bg-card/95 p-2 shadow-md opacity-0 translate-y-1 transition duration-150 group-hover:opacity-100 group-hover:translate-y-0 group-focus-within:opacity-100 group-focus-within:translate-y-0">
          <p className="mb-1 typo-helper font-semibold uppercase tracking-wide text-muted-foreground">
            REPL code preview
          </p>
          <pre className="max-h-40 overflow-auto rounded-md bg-muted/25 p-2 typo-helper leading-relaxed text-foreground whitespace-pre-wrap wrap-break-word font-mono">
            {codePreview}
          </pre>
        </div>
      )}

      {!isExpanded && errorDetails && (
        <div className="pointer-events-none absolute left-full top-16 z-50 ml-2 w-72 rounded-lg border border-destructive/30 bg-card/95 p-2 shadow-md opacity-0 translate-y-1 transition duration-150 group-hover:opacity-100 group-hover:translate-y-0 group-focus-within:opacity-100 group-focus-within:translate-y-0">
          <p className="mb-1 typo-helper font-semibold uppercase tracking-wide text-destructive">
            Error details
          </p>
          <p className="typo-caption leading-snug text-foreground whitespace-pre-wrap wrap-break-word">
            {errorDetails.message}
          </p>
          {errorDetails.code && (
            <p className="mt-1 typo-helper text-muted-foreground">Code: {errorDetails.code}</p>
          )}
        </div>
      )}

      {/* Expanded detail panel */}
      {isExpanded && (
        <div className="absolute left-full top-0 ml-2 z-50 w-72 rounded-xl border border-border-subtle/80 bg-card p-3 text-foreground shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <div className="shrink-0 rounded-md p-1 node-tint-header">
              <Icon className="size-4 node-color-text" aria-hidden />
            </div>
            <span className="text-xs font-semibold node-color-text">{meta.label}</span>
          </div>

          <p className="text-xs font-semibold text-foreground mb-1 wrap-break-word">{data.label}</p>

          {data.toolName && (data.type === "tool" || data.type === "repl") && (
            <div className="mb-2">
              <NodeBadge className="border-border-subtle bg-muted/50 px-2 py-0.5">
                <span className="whitespace-pre-wrap wrap-break-word">{data.toolName}</span>
              </NodeBadge>
            </div>
          )}

          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <NodeBadge className="border-border-subtle bg-muted/40 px-2 py-0.5">
              {formatActorLabel(data.actorKind)}
            </NodeBadge>
            {typeof data.depth === "number" && (
              <NodeBadge className="border-border-subtle bg-muted/40 px-2 py-0.5">
                Depth {data.depth}
              </NodeBadge>
            )}
            {data.actorId && (
              <NodeBadge className="border-border-subtle bg-muted/40 px-2 py-0.5 whitespace-pre-wrap wrap-break-word">
                {data.actorId}
              </NodeBadge>
            )}
          </div>

          {data.summary && (
            <p className="typo-caption leading-relaxed text-muted-foreground whitespace-pre-wrap wrap-break-word mb-2">
              {data.summary}
            </p>
          )}

          {codePreview && data.type === "repl" && (
            <div className="mb-2">
              <p className="mb-1 typo-helper font-semibold uppercase tracking-wide text-muted-foreground">
                REPL code preview
              </p>
              <pre className="max-h-40 overflow-auto rounded-md border border-border-subtle bg-muted/40 p-2 typo-helper leading-relaxed whitespace-pre-wrap wrap-break-word font-mono">
                {codePreview}
              </pre>
            </div>
          )}

          {errorDetails && (
            <div className="mb-2 rounded-md border border-destructive/40 bg-destructive/5 p-2">
              <p className="mb-1 typo-helper font-semibold uppercase tracking-wide text-destructive">
                Error details
              </p>
              <p className="typo-caption leading-relaxed text-foreground whitespace-pre-wrap wrap-break-word">
                {errorDetails.message}
              </p>
              {errorDetails.code && (
                <p className="mt-1 typo-helper text-muted-foreground">Code: {errorDetails.code}</p>
              )}
              {errorDetails.trace && (
                <pre className="mt-2 max-h-28 overflow-auto rounded border border-red-500/20 bg-card/60 p-2 typo-helper leading-relaxed whitespace-pre-wrap wrap-break-word font-mono">
                  {errorDetails.trace}
                </pre>
              )}
            </div>
          )}

          {trajectory && (
            <div className="mb-2 rounded-md border border-border-subtle bg-muted/20 p-2">
              <p className="mb-2 typo-helper font-semibold uppercase tracking-wide text-muted-foreground">
                Thought → Action → Observation
              </p>
              <div className="flex flex-col gap-2">
                {trajectory.thought && (
                  <div>
                    <p className="typo-helper font-semibold text-foreground/80">Thought</p>
                    <p className="typo-caption leading-relaxed text-muted-foreground whitespace-pre-wrap wrap-break-word">
                      {trajectory.thought}
                    </p>
                  </div>
                )}
                {trajectory.action && (
                  <div>
                    <p className="typo-helper font-semibold text-foreground/80">Action</p>
                    <p className="typo-caption leading-relaxed text-muted-foreground whitespace-pre-wrap wrap-break-word">
                      {trajectory.action}
                    </p>
                  </div>
                )}
                {trajectory.observation && (
                  <div>
                    <p className="typo-helper font-semibold text-foreground/80">Observation</p>
                    <p className="typo-caption leading-relaxed text-muted-foreground whitespace-pre-wrap wrap-break-word">
                      {trajectory.observation}
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="flex items-center gap-2 typo-helper text-muted-foreground">
            <span className={cn("size-1.5 rounded-full shrink-0", statusDot)} />
            <span className="capitalize">{data.status}</span>
            {data.elapsedMs != null && data.elapsedMs > 0 && (
              <span className="tabular-nums">{formatElapsed(data.elapsedMs)}</span>
            )}
            {data.count > 1 && <span>{data.count} steps</span>}
          </div>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="bg-border! w-1.5! h-1.5!" />
    </div>
  );
});

export { GraphStepNode };
