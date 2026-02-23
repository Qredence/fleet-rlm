import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { ArtifactStepType } from "@/stores/artifactStore";
import { cn } from "@/components/ui/utils";
import {
  NODE_WIDTH,
  STEP_TYPE_META,
} from "@/features/artifacts/components/GraphStepNode.constants";
import {
  extractErrorDetails,
  extractReplCodePreview,
  extractTrajectoryChain,
} from "@/features/artifacts/components/graphNodeDetailParsers";

// ── Node data shape ─────────────────────────────────────────────────

export interface GraphStepNodeData {
  label: string;
  type: ArtifactStepType;
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
}: NodeProps<GraphStepNodeData>) {
  const meta = STEP_TYPE_META[data.type];
  const Icon = meta.Icon;
  const isExpanded = data.expanded === true;
  const codePreview = extractReplCodePreview(data);
  const errorDetails = extractErrorDetails(data);
  const trajectory = extractTrajectoryChain(data);

  const statusDot =
    data.status === "streaming"
      ? "bg-yellow-400 animate-pulse"
      : data.status === "error"
        ? "bg-red-500"
        : "bg-emerald-500";

  return (
    <div
      className={cn(
        "group relative rounded-xl border bg-card text-foreground shadow-sm transition-shadow",
        selected
          ? "ring-2 ring-accent border-accent"
          : "border-border-subtle hover:border-border-strong",
      )}
      style={{
        width: NODE_WIDTH,
        borderLeftWidth: 3,
        borderLeftColor: meta.color,
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-border !w-2 !h-2"
      />

      <div className="flex items-start gap-2.5 px-3 py-2.5">
        {/* Icon */}
        <div
          className="mt-0.5 shrink-0 rounded-md p-1"
          style={{
            backgroundColor: `color-mix(in srgb, ${meta.color} 15%, transparent)`,
          }}
        >
          <Icon
            className="size-3.5"
            style={{ color: meta.color }}
            aria-hidden
          />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Header row */}
          <div className="flex items-center gap-1.5 min-w-0">
            <span
              className={cn("text-xs font-semibold", !isExpanded && "truncate")}
            >
              {data.label}
            </span>
            {data.count > 1 && (
              <span
                className="shrink-0 rounded-full px-1.5 py-px text-[10px] font-medium leading-tight"
                style={{
                  backgroundColor: `color-mix(in srgb, ${meta.color} 20%, transparent)`,
                  color: meta.color,
                }}
              >
                ×{data.count}
              </span>
            )}
          </div>

          {data.toolName && (data.type === "tool" || data.type === "repl") && (
            <div className="mt-1 min-w-0">
              <span
                className="inline-flex max-w-full items-center rounded-full border border-border-subtle bg-muted/50 px-1.5 py-px text-[10px] font-medium leading-tight text-foreground/90"
                title={data.toolName}
              >
                <span className="truncate">{data.toolName}</span>
              </span>
            </div>
          )}

          {/* Summary */}
          {data.summary && (
            <p
              className={cn(
                "mt-0.5 text-[11px] leading-snug text-muted-foreground",
                !isExpanded && "line-clamp-1",
              )}
            >
              {data.summary}
            </p>
          )}

          {/* Footer badges */}
          <div className="mt-1.5 flex items-center gap-2">
            {/* Status dot */}
            <span
              className={cn("size-1.5 rounded-full shrink-0", statusDot)}
              aria-label={data.status}
            />
            <span className="text-[10px] text-muted-foreground capitalize">
              {data.status}
            </span>

            {/* Elapsed time */}
            {data.elapsedMs != null && data.elapsedMs > 0 && (
              <span className="text-[10px] text-muted-foreground tabular-nums">
                {formatElapsed(data.elapsedMs)}
              </span>
            )}
          </div>
        </div>
      </div>

      {!isExpanded && codePreview && (
        <div className="pointer-events-none absolute left-full top-0 z-50 ml-2 w-72 max-w-[24rem] rounded-lg border border-border-subtle bg-card/95 p-2 shadow-lg opacity-0 translate-y-1 transition duration-150 group-hover:opacity-100 group-hover:translate-y-0 group-focus-within:opacity-100 group-focus-within:translate-y-0">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            REPL code preview
          </p>
          <pre className="max-h-40 overflow-auto rounded-md bg-muted/40 p-2 text-[10px] leading-relaxed text-foreground whitespace-pre-wrap break-words font-mono">
            {codePreview}
          </pre>
        </div>
      )}

      {!isExpanded && errorDetails && (
        <div className="pointer-events-none absolute left-full top-16 z-50 ml-2 w-72 rounded-lg border border-red-500/40 bg-card/95 p-2 shadow-lg opacity-0 translate-y-1 transition duration-150 group-hover:opacity-100 group-hover:translate-y-0 group-focus-within:opacity-100 group-focus-within:translate-y-0">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-red-500">
            Error details
          </p>
          <p className="text-[11px] leading-snug text-foreground whitespace-pre-wrap break-words">
            {errorDetails.message}
          </p>
          {errorDetails.code && (
            <p className="mt-1 text-[10px] text-muted-foreground">
              Code: {errorDetails.code}
            </p>
          )}
        </div>
      )}

      {/* Expanded detail panel */}
      {isExpanded && (
        <div className="absolute left-full top-0 ml-2 z-50 w-72 rounded-xl border border-border-subtle bg-card shadow-lg p-3 text-foreground">
          <div className="flex items-center gap-2 mb-2">
            <div
              className="shrink-0 rounded-md p-1"
              style={{
                backgroundColor: `color-mix(in srgb, ${meta.color} 15%, transparent)`,
              }}
            >
              <Icon
                className="size-4"
                style={{ color: meta.color }}
                aria-hidden
              />
            </div>
            <span
              className="text-xs font-semibold"
              style={{ color: meta.color }}
            >
              {meta.label}
            </span>
          </div>

          <p className="text-xs font-semibold text-foreground mb-1 break-words">
            {data.label}
          </p>

          {data.toolName && (data.type === "tool" || data.type === "repl") && (
            <div className="mb-2">
              <span
                className="inline-flex max-w-full items-center rounded-full border border-border-subtle bg-muted/50 px-2 py-0.5 text-[10px] font-medium leading-tight text-foreground/90"
                title={data.toolName}
              >
                <span className="truncate">{data.toolName}</span>
              </span>
            </div>
          )}

          {data.summary && (
            <p className="text-[11px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-words mb-2">
              {data.summary}
            </p>
          )}

          {codePreview && data.type === "repl" && (
            <div className="mb-2">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                REPL code preview
              </p>
              <pre className="max-h-40 overflow-auto rounded-md border border-border-subtle bg-muted/40 p-2 text-[10px] leading-relaxed whitespace-pre-wrap break-words font-mono">
                {codePreview}
              </pre>
            </div>
          )}

          {errorDetails && (
            <div className="mb-2 rounded-md border border-red-500/40 bg-red-500/5 p-2">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-red-500">
                Error details
              </p>
              <p className="text-[11px] leading-relaxed text-foreground whitespace-pre-wrap break-words">
                {errorDetails.message}
              </p>
              {errorDetails.code && (
                <p className="mt-1 text-[10px] text-muted-foreground">
                  Code: {errorDetails.code}
                </p>
              )}
              {errorDetails.trace && (
                <pre className="mt-2 max-h-28 overflow-auto rounded border border-red-500/20 bg-card/60 p-2 text-[10px] leading-relaxed whitespace-pre-wrap break-words font-mono">
                  {errorDetails.trace}
                </pre>
              )}
            </div>
          )}

          {trajectory && (
            <div className="mb-2 rounded-md border border-border-subtle bg-muted/20 p-2">
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Thought → Action → Observation
              </p>
              <div className="space-y-2">
                {trajectory.thought && (
                  <div>
                    <p className="text-[10px] font-semibold text-foreground/80">
                      Thought
                    </p>
                    <p className="text-[11px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-words">
                      {trajectory.thought}
                    </p>
                  </div>
                )}
                {trajectory.action && (
                  <div>
                    <p className="text-[10px] font-semibold text-foreground/80">
                      Action
                    </p>
                    <p className="text-[11px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-words">
                      {trajectory.action}
                    </p>
                  </div>
                )}
                {trajectory.observation && (
                  <div>
                    <p className="text-[10px] font-semibold text-foreground/80">
                      Observation
                    </p>
                    <p className="text-[11px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-words">
                      {trajectory.observation}
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <span className={cn("size-1.5 rounded-full shrink-0", statusDot)} />
            <span className="capitalize">{data.status}</span>
            {data.elapsedMs != null && data.elapsedMs > 0 && (
              <span className="tabular-nums">
                {formatElapsed(data.elapsedMs)}
              </span>
            )}
            {data.count > 1 && <span>{data.count} steps</span>}
          </div>
        </div>
      )}

      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-border !w-2 !h-2"
      />
    </div>
  );
});

export { GraphStepNode };
