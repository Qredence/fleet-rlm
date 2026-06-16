import { memo } from "react";
import { IconActivityHeartbeat, IconExternalLink } from "@tabler/icons-react";

import { buildMlflowTraceUrl } from "@/lib/mlflow/trace-url";

import { getToolStatus } from "../utils/format-tool";
import { cn } from "../utils/cn";
import { ToolRowBase } from "./tool-row-base";

function formatDuration(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "";
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}s`;
}

function stringifyDetails(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function mlflowSpanTraceUrl(part: any): string | undefined {
  const span = part?.mlflowSpan;
  if (typeof span?.traceUrl === "string" && span.traceUrl.trim()) return span.traceUrl;
  if (typeof span?.traceId !== "string" || !span.traceId.trim()) return undefined;
  return buildMlflowTraceUrl({
    traceId: span.traceId,
    trackingUri:
      typeof span.trackingUri === "string" && span.trackingUri.trim()
        ? span.trackingUri
        : "http://127.0.0.1:5001",
    experimentId: typeof span.experimentId === "string" ? span.experimentId : undefined,
  });
}

export function mlflowSpanTitle(part: any): string {
  const title =
    typeof part?.input?.name === "string"
      ? part.input.name
      : typeof part?.title === "string"
        ? part.title
        : "";
  return title.trim() || "MLflow span";
}

export function mlflowSpanSubtitle(part: any): string {
  const status = part?.mlflowSpan?.status;
  const duration = formatDuration(part?.mlflowSpan?.durationMs);
  return [status, duration].filter(Boolean).join(" - ");
}

export type MlflowSpanToolProps = {
  part: any;
  chatStatus?: string;
  defaultOpen?: boolean;
};

export const MlflowSpanTool = memo(function MlflowSpanTool({
  part,
  chatStatus,
  defaultOpen = false,
}: MlflowSpanToolProps) {
  const { isPending, isError } = getToolStatus(part, chatStatus);
  const traceUrl = mlflowSpanTraceUrl(part);
  const inputDetails = stringifyDetails(part.input);
  const outputDetails = stringifyDetails(part.output);
  const hasDetails = Boolean(inputDetails || outputDetails || part.errorText);
  const completeLabel = mlflowSpanTitle(part);
  const subtitle = mlflowSpanSubtitle(part);

  return (
    <ToolRowBase
      icon={<IconActivityHeartbeat className={cn("size-3", isError && "text-destructive")} />}
      completeLabel={completeLabel}
      shimmerLabel={`Running ${completeLabel}`}
      isAnimating={isPending}
      subtitle={subtitle}
      expandable={hasDetails}
      defaultOpen={defaultOpen}
      trailingContent={
        traceUrl ? (
          <a
            href={traceUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center text-an-foreground-muted/60 transition-colors hover:text-an-foreground"
            aria-label="Open MLflow trace"
            onClick={(event) => event.stopPropagation()}
          >
            <IconExternalLink className="size-3" />
          </a>
        ) : undefined
      }
    >
      <div className="space-y-2 pl-5 text-2xs text-an-foreground-muted/80">
        {inputDetails ? (
          <div>
            <div className="mb-1 font-medium text-an-foreground-muted">Input</div>
            <pre className="max-h-40 overflow-auto rounded-md border border-border/60 bg-muted/30 p-2 whitespace-pre-wrap">
              {inputDetails}
            </pre>
          </div>
        ) : null}
        {outputDetails || part.errorText ? (
          <div>
            <div className="mb-1 font-medium text-an-foreground-muted">
              {part.errorText ? "Error" : "Output"}
            </div>
            <pre className="max-h-40 overflow-auto rounded-md border border-border/60 bg-muted/30 p-2 whitespace-pre-wrap">
              {part.errorText || outputDetails}
            </pre>
          </div>
        ) : null}
      </div>
    </ToolRowBase>
  );
});
