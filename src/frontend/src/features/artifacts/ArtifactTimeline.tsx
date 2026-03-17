import { Brain, Terminal, Wrench, Database, FileOutput } from "lucide-react";

import type { ArtifactStepType, ExecutionStep } from "@/lib/data/artifactTypes";
import { cn } from "@/lib/utils/cn";
import { ScrollArea } from "@/components/ui/scroll-area";
import { summarizeArtifactStep } from "@/features/artifacts/parsers/artifactPayloadSummaries";
import { STEP_TYPE_META } from "@/features/artifacts/GraphStepNode.constants";
import { extractToolBadgeFromStep } from "@/features/artifacts/graphToolBadge";

interface ArtifactTimelineProps {
  steps: ExecutionStep[];
  activeStepId?: string;
  onSelectStep: (id: string) => void;
}

const ICONS: Record<ArtifactStepType, typeof Brain> = {
  llm: Brain,
  repl: Terminal,
  tool: Wrench,
  memory: Database,
  output: FileOutput,
};

function mapStatus(step: ExecutionStep): "pending" | "active" | "complete" | "error" {
  const label = step.label?.toLowerCase() ?? "";
  if (label.includes("error") || label.includes("failed")) return "error";
  // If the step has output, treat as complete
  if (step.output != null) return "complete";
  return "active";
}

function formatActorLabel(step: ExecutionStep): string | null {
  const actorId = typeof step.actor_id === "string" ? step.actor_id.trim() : "";

  if (step.actor_kind === "sub_agent") {
    return actorId ? `Sub-agent ${actorId}` : "Sub-agent";
  }

  if (step.actor_kind === "delegate") {
    return actorId ? `Delegate ${actorId}` : "Delegate";
  }

  if (step.actor_kind === "unknown") {
    return actorId || "Unknown actor";
  }

  if (actorId && actorId.toLowerCase() !== "root_rlm") {
    return actorId;
  }

  return null;
}

function formatRunOffset(ms: number | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `T+${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `T+${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `T+${minutes}m ${remainder}s`;
}

function formatElapsedLabel(ms: number | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return null;
  if (ms < 1000) return `+${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `+${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `+${minutes}m ${remainder}s`;
}

function formatSummary(step: ExecutionStep): string {
  const summary = summarizeArtifactStep(step).trim();
  const labelPrefix = `${step.label.trim().toLowerCase()}:`;
  if (summary.toLowerCase().startsWith(labelPrefix)) {
    return summary.slice(labelPrefix.length).trim();
  }
  return summary;
}

export function ArtifactTimeline({ steps, activeStepId, onSelectStep }: ArtifactTimelineProps) {
  const ordered = [...steps].sort((a, b) => {
    const aSeq = a.sequence;
    const bSeq = b.sequence;
    if (aSeq != null && bSeq != null && aSeq !== bSeq) return aSeq - bSeq;
    if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp;
    return a.id.localeCompare(b.id);
  });

  if (ordered.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
        Timeline fills in as steps arrive.
      </div>
    );
  }

  const firstTimestamp = ordered[0]?.timestamp ?? 0;
  const lastTimestamp = ordered[ordered.length - 1]?.timestamp ?? firstTimestamp;
  const toolCount = ordered.filter((step) => step.type === "tool").length;
  const activeStep = ordered.find((step) => step.id === activeStepId);
  const durationLabel = formatElapsedLabel(lastTimestamp - firstTimestamp);

  return (
    <ScrollArea className="h-full pr-1">
      <div className="space-y-3 pb-2">
        <section className="rounded-bubble border border-border-subtle/80 bg-linear-to-br from-card via-card/95 to-muted/15 p-3.5 shadow-[0_18px_48px_-36px_rgba(15,23,42,0.24)]">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                Execution timeline
              </p>
              {activeStep ? (
                <p className="mt-1 text-sm font-medium text-foreground">{activeStep.label}</p>
              ) : null}
            </div>

            {activeStep ? (
              <span className="inline-flex shrink-0 items-center rounded-full border border-border-subtle/80 bg-background/80 px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.16em] text-foreground/80">
                {STEP_TYPE_META[activeStep.type].label}
              </span>
            ) : null}
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <span className="inline-flex items-center gap-2 rounded-full border border-border-subtle/70 bg-background/75 px-3 py-1.5">
              <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                Steps
              </span>
              <span className="text-sm font-semibold text-foreground">{ordered.length}</span>
            </span>

            <span className="inline-flex items-center gap-2 rounded-full border border-border-subtle/70 bg-background/75 px-3 py-1.5">
              <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                Tools
              </span>
              <span className="text-sm font-semibold text-foreground">{toolCount}</span>
            </span>

            <span className="inline-flex items-center gap-2 rounded-full border border-border-subtle/70 bg-background/75 px-3 py-1.5">
              <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                Runtime
              </span>
              <span className="text-sm font-semibold text-foreground">
                {durationLabel ?? "Live"}
              </span>
            </span>

            {activeStep ? (
              <span className="inline-flex max-w-full items-center gap-2 rounded-full border border-border-subtle/70 bg-background/75 px-3 py-1.5">
                <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                  Focused
                </span>
                <span className="truncate text-sm text-foreground">{activeStep.label}</span>
              </span>
            ) : null}
          </div>
        </section>

        <div className="relative space-y-2.5">
          {ordered.map((step, index) => {
            const Icon = ICONS[step.type] ?? Brain;
            const meta = STEP_TYPE_META[step.type];
            const selected = step.id === activeStepId;
            const summary = formatSummary(step);
            const showSummary =
              summary.length > 0 &&
              summary.trim().toLowerCase() !== step.label.trim().toLowerCase();
            const actorLabel = formatActorLabel(step);
            const toolBadge = extractToolBadgeFromStep(step);
            const elapsedLabel =
              index === 0 ? "Start" : formatRunOffset(step.timestamp - firstTimestamp);
            const status = mapStatus(step);
            const statusTone =
              status === "error"
                ? "text-destructive border-destructive/25 bg-destructive/6"
                : "text-accent border-accent/30 bg-accent/8";

            const hasConnector = index < ordered.length - 1;
            const chips = [
              actorLabel,
              toolBadge.toolName && step.type !== "tool" ? toolBadge.toolName : null,
            ].filter((value): value is string => Boolean(value));

            return (
              <div key={step.id} className="relative" style={{ paddingInlineStart: "2.8rem" }}>
                {hasConnector ? (
                  <div className="absolute left-[0.95rem] top-10 -bottom-3 w-px bg-linear-to-b from-border-strong/50 via-border-subtle/40 to-transparent" />
                ) : null}

                <div
                  className="absolute left-0 top-3 flex size-8 items-center justify-center rounded-full border border-border-subtle/80 bg-background/92 shadow-sm"
                  style={{
                    boxShadow: selected
                      ? `0 0 0 1px color-mix(in srgb, ${meta.color} 35%, transparent)`
                      : undefined,
                  }}
                >
                  <div
                    className="rounded-full p-1.25"
                    style={{
                      backgroundColor: `color-mix(in srgb, ${meta.color} 18%, transparent)`,
                    }}
                  >
                    <Icon className="size-3.5" style={{ color: meta.color }} aria-hidden />
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => onSelectStep(step.id)}
                  className={cn(
                    "group w-full rounded-[20px] border px-3.5 py-3.5 text-left transition-[border-color,background-color,box-shadow]",
                    selected
                      ? "border-accent/40 bg-accent/7 shadow-[0_18px_44px_-34px_rgba(59,130,246,0.38)]"
                      : "border-border-subtle/80 bg-card/55 hover:border-border-strong hover:bg-card/85",
                  )}
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
                        <span className="rounded-full border border-border-subtle/70 bg-background/70 px-2 py-0.5 text-foreground/75">
                          Step {step.sequence ?? index + 1}
                        </span>
                        <span style={{ color: meta.color }}>{meta.label}</span>
                        {elapsedLabel ? (
                          <span className="font-medium normal-case tracking-normal text-muted-foreground tabular-nums">
                            {elapsedLabel}
                          </span>
                        ) : null}
                      </div>

                      {status === "active" || status === "error" ? (
                        <span
                          className={cn(
                            "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-medium",
                            statusTone,
                          )}
                        >
                          {status === "active" ? "Running" : "Issue"}
                        </span>
                      ) : null}
                    </div>

                    <h3 className="mt-1.5 text-sm font-medium leading-snug text-foreground">
                      {step.label}
                    </h3>

                    {showSummary ? (
                      <p className="mt-2 text-xs leading-5 text-muted-foreground whitespace-pre-wrap wrap-break-word">
                        {summary}
                      </p>
                    ) : null}

                    {chips.length > 0 ? (
                      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                        {chips.map((chip) => (
                          <span
                            key={`${step.id}-${chip}`}
                            className="inline-flex items-center rounded-full border border-border-subtle/80 bg-background/65 px-2.5 py-1 text-[10px] font-medium text-foreground/80"
                          >
                            {chip}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </ScrollArea>
  );
}
