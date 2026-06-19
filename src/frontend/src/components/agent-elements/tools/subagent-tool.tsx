import { memo, useEffect, useState } from "react";
import { toolRegistry } from "./tool-registry";
import { GenericTool } from "./generic-tool";
import { getToolStatus } from "../utils/format-tool";
import { cn } from "../utils/cn";
import { ToolRowBase } from "./tool-row-base";

export type SubagentToolProps = {
  part: any;
  nestedTools?: any[];
  chatStatus?: string;
};

const MAX_VISIBLE_TOOLS = 5;

function formatElapsedTime(ms: number): string {
  if (ms < 1000) return "";
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (remainingSeconds === 0) return `${minutes}m`;
  return `${minutes}m ${remainingSeconds}s`;
}

function subagentLabel(part: any): string {
  const raw =
    part?.input?.subagent_type ??
    part?.input?.agent_type ??
    part?.input?.name ??
    (part?.type === "tool-Agent" ? "Agent" : "Task");
  const label = String(raw || "").trim();
  return label || "Agent";
}

export const SubagentTool = memo(function SubagentTool({
  part,
  nestedTools = [],
  chatStatus,
}: SubagentToolProps) {
  const label = subagentLabel(part);
  const { isPending, isInterrupted } = getToolStatus(part, chatStatus);
  const description = part.input?.description || "";
  const [elapsedMs, setElapsedMs] = useState(0);
  const startedAt =
    (part.callProviderMetadata?.custom?.startedAt as number | undefined) ??
    (part.startedAt as number | undefined);
  const hasNestedTools = nestedTools.length > 0;
  const outputDuration =
    part.output?.totalDurationMs || part.output?.duration || part.output?.duration_ms;

  useEffect(() => {
    if (isPending && startedAt) {
      setElapsedMs(Date.now() - startedAt);
      const interval = setInterval(() => {
        setElapsedMs(Date.now() - startedAt);
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [isPending, startedAt]);

  const subtitle = (() => {
    if (isPending && hasNestedTools) {
      const lastTool = nestedTools[nestedTools.length - 1];
      const meta = lastTool ? toolRegistry[lastTool.type] : null;
      if (meta) {
        const title = meta.title(lastTool);
        const subtitle = meta.subtitle?.(lastTool);
        return subtitle ? `${title} ${subtitle}` : title;
      }
    }

    if (!description) return "";
    return description.length > 60 ? `${description.slice(0, 57)}...` : description;
  })();

  const elapsedTimeDisplay = formatElapsedTime(
    !isPending && outputDuration ? outputDuration : elapsedMs,
  );

  if (isInterrupted && !part.output) {
    return <ToolRowBase completeLabel={`${label} interrupted`} isAnimating={false} />;
  }

  return (
    <div className="an-tool-task">
      <ToolRowBase
        completeLabel={`${label} completed`}
        shimmerLabel={`Running ${label.toLowerCase()}`}
        isAnimating={isPending}
        detail={subtitle}
        expandable={hasNestedTools}
        trailingContent={
          elapsedTimeDisplay ? (
            <span className="font-normal tabular-nums shrink-0 text-an-foreground-muted/60">
              {elapsedTimeDisplay}
            </span>
          ) : undefined
        }
      >
        <div className="relative">
          {isPending && nestedTools.length > MAX_VISIBLE_TOOLS && (
            <div className="absolute inset-x-0 top-0 h-8 z-10 pointer-events-none bg-linear-to-b from-an-background to-transparent" />
          )}
          <div
            className={cn(
              nestedTools.length > 1 ? "space-y-2" : "space-y-0",
              isPending &&
                nestedTools.length > MAX_VISIBLE_TOOLS &&
                "overflow-y-auto max-h-[120px]",
            )}
          >
            {nestedTools.map((nestedPart, idx) => {
              const nestedMeta = toolRegistry[nestedPart.type];
              if (!nestedMeta) {
                return (
                  <ToolRowBase
                    key={idx}
                    completeLabel={nestedPart.type?.replace("tool-", "") ?? "Tool"}
                    isAnimating={false}
                  />
                );
              }
              const { isPending: nestedIsPending, isError: nestedIsError } = getToolStatus(
                nestedPart,
                chatStatus,
              );
              return (
                <GenericTool
                  key={idx}
                  icon={nestedMeta.icon}
                  title={nestedMeta.title(nestedPart)}
                  subtitle={nestedMeta.subtitle?.(nestedPart)}
                  isPending={nestedIsPending}
                  isError={nestedIsError}
                />
              );
            })}
          </div>
        </div>
      </ToolRowBase>
    </div>
  );
});
