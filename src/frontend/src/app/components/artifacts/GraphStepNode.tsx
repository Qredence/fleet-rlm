import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import {
  Brain,
  Terminal,
  Wrench,
  Database,
  FileOutput,
} from "lucide-react";
import type { ArtifactStepType } from "../../stores/artifactStore";
import { cn } from "../ui/utils";

// ── Shared constants (re-exported for legend + edges) ───────────────

export const STEP_TYPE_META: Record<
  ArtifactStepType,
  { color: string; label: string; Icon: typeof Brain }
> = {
  llm: { color: "var(--chart-3)", label: "LLM", Icon: Brain },
  repl: { color: "var(--chart-4)", label: "REPL", Icon: Terminal },
  tool: { color: "var(--chart-2)", label: "Tool", Icon: Wrench },
  memory: { color: "var(--chart-1)", label: "Memory", Icon: Database },
  output: { color: "var(--accent)", label: "Output", Icon: FileOutput },
};

// ── Node data shape ─────────────────────────────────────────────────

export interface GraphStepNodeData {
  label: string;
  type: ArtifactStepType;
  summary: string;
  count: number;
  representativeStepId: string;
  elapsedMs?: number;
  status: "streaming" | "complete" | "error";
  expanded?: boolean;
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

const NODE_WIDTH = 220;

const GraphStepNode = memo(function GraphStepNode({
  data,
  selected,
}: NodeProps<GraphStepNodeData>) {
  const meta = STEP_TYPE_META[data.type];
  const Icon = meta.Icon;
  const isExpanded = data.expanded === true;

  const statusDot =
    data.status === "streaming"
      ? "bg-yellow-400 animate-pulse"
      : data.status === "error"
        ? "bg-red-500"
        : "bg-emerald-500";

  return (
    <div
      className={cn(
        "relative rounded-xl border bg-card text-foreground shadow-sm transition-shadow",
        selected
          ? "ring-2 ring-accent border-accent"
          : "border-border-subtle hover:border-border-strong",
      )}
      style={{ width: NODE_WIDTH, borderLeftWidth: 3, borderLeftColor: meta.color }}
    >
      <Handle type="target" position={Position.Top} className="!bg-border !w-2 !h-2" />

      <div className="flex items-start gap-2.5 px-3 py-2.5">
        {/* Icon */}
        <div
          className="mt-0.5 shrink-0 rounded-md p-1"
          style={{ backgroundColor: `color-mix(in srgb, ${meta.color} 15%, transparent)` }}
        >
          <Icon className="size-3.5" style={{ color: meta.color }} aria-hidden />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Header row */}
          <div className="flex items-center gap-1.5">
            <span className={cn("text-xs font-semibold", !isExpanded && "truncate")}>{data.label}</span>
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

          {/* Summary */}
          {data.summary && (
            <p className={cn(
              "mt-0.5 text-[11px] leading-snug text-muted-foreground",
              !isExpanded && "line-clamp-1",
            )}>
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

      {/* Expanded detail panel */}
      {isExpanded && (
        <div className="absolute left-full top-0 ml-2 z-50 w-72 rounded-xl border border-border-subtle bg-card shadow-lg p-3 text-foreground">
          <div className="flex items-center gap-2 mb-2">
            <div
              className="shrink-0 rounded-md p-1"
              style={{ backgroundColor: `color-mix(in srgb, ${meta.color} 15%, transparent)` }}
            >
              <Icon className="size-4" style={{ color: meta.color }} aria-hidden />
            </div>
            <span className="text-xs font-semibold" style={{ color: meta.color }}>
              {meta.label}
            </span>
          </div>

          <p className="text-xs font-semibold text-foreground mb-1 break-words">
            {data.label}
          </p>

          {data.summary && (
            <p className="text-[11px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-words mb-2">
              {data.summary}
            </p>
          )}

          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <span className={cn("size-1.5 rounded-full shrink-0", statusDot)} />
            <span className="capitalize">{data.status}</span>
            {data.elapsedMs != null && data.elapsedMs > 0 && (
              <span className="tabular-nums">{formatElapsed(data.elapsedMs)}</span>
            )}
            {data.count > 1 && (
              <span>{data.count} steps</span>
            )}
          </div>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="!bg-border !w-2 !h-2" />
    </div>
  );
});

export { GraphStepNode };
export { NODE_WIDTH };
