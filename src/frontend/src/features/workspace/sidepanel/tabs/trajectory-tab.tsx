import { useMemo } from "react";
import { Brain, Loader2, TriangleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyPanel } from "@/components/product/empty-panel";
import { buildAssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import {
  type SandboxActivityEvent,
  SandboxActivityPanel,
} from "@/features/workspace/inspection/sandbox-activity-panel";
import {
  DetailBlock,
  executionSectionState,
  renderBadges,
  renderExecutionSectionDetails,
  statusTone,
} from "@/features/workspace/inspection/inspector-ui";
import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import {
  formatTraceDuration,
  formatTraceDurationMs,
  formatTraceOutputChars,
  formatTraceTokens,
  getTraceSpanStatus,
  sortTraceSpans,
  traceSpanKind,
  traceSpanLabel,
} from "@/features/workspace/screen/workspace-session-trace-model";
import { type SessionTraceDebugResponse } from "@/lib/rlm-api/sessions";
import { cn } from "@/lib/utils";
import { buildChatDisplayItems } from "@/lib/workspace/chat-display-items";
import {
  TrajectoryChain,
  TrajectoryChainStep,
} from "@/features/workspace/inspection/trajectory-chain";

import { type SessionTraceState, selectedTurnStatus } from "../use-session-trace";

type AssistantTurn = Extract<
  ReturnType<typeof buildChatDisplayItems>[number],
  { kind: "assistant_turn" }
>;

const trajectoryCardClass = cn(inspectorStyles.card.root, "max-w-full");
const trajectoryHeaderClass = cn(inspectorStyles.card.header, "min-w-0 max-w-full overflow-hidden");
const trajectoryContentClass = cn(
  inspectorStyles.card.content,
  "min-w-0 max-w-full overflow-hidden",
);
export function selectedTurnHasTimeline(selectedTurn: AssistantTurn | null): boolean {
  if (!selectedTurn) return false;
  const model = buildAssistantContentModel(selectedTurn);
  return model.trajectory.hasContent || model.execution.hasContent || model.answer.hasContent;
}

export function SelectedTurnTrajectory({ selectedTurn }: { selectedTurn: AssistantTurn | null }) {
  const model = useMemo(
    () => (selectedTurn ? buildAssistantContentModel(selectedTurn) : null),
    [selectedTurn],
  );

  const sandboxEvents = useMemo<SandboxActivityEvent[]>(
    () => extractSandboxEvents(selectedTurn),
    [selectedTurn],
  );

  if (!selectedTurn || !model) return null;

  const status = selectedTurnStatus(model);
  const tone = statusTone(status);
  const hasTimeline =
    model.trajectory.hasContent ||
    model.execution.hasContent ||
    model.answer.hasContent ||
    sandboxEvents.length > 0;

  if (!hasTimeline) return null;

  const isRunning = status === "running";

  return (
    <div className="workspace-trajectory-content flex min-w-0 max-w-full flex-col gap-3 overflow-hidden">
      <Card className={trajectoryCardClass}>
        <CardHeader className={trajectoryHeaderClass}>
          <div className="flex min-w-0 items-center justify-between gap-3">
            <div className="min-w-0">
              <CardTitle className="typo-label font-medium text-foreground">
                Selected turn context
              </CardTitle>
              <CardDescription className="max-w-full typo-caption wrap-break-word">
                Live transcript reasoning and execution detail for the selected assistant turn.
              </CardDescription>
            </div>
            <Badge variant={tone.variant} className={inspectorStyles.badge.status}>
              {tone.label}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className={trajectoryContentClass}>
          <div className={cn(inspectorStyles.badge.row, "min-w-0 max-w-full overflow-hidden")}>
            {model.summary.trajectoryCount > 0 ? (
              <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                {model.summary.trajectoryCount} trajectories
              </Badge>
            ) : null}
            {model.summary.toolSessionCount > 0 ? (
              <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                {model.summary.toolSessionCount} tool sessions
              </Badge>
            ) : null}
            {model.summary.sourceCount > 0 ? (
              <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                {model.summary.sourceCount} sources
              </Badge>
            ) : null}
            {renderBadges(model.summary.runtimeBadges, "secondary")}
          </div>
        </CardContent>
      </Card>

      <TrajectoryChain>
        {model.trajectory.overview ? (
          <TrajectoryChainStep
            title="Thinking"
            description="Overview of the reasoning path for this turn."
            status={model.trajectory.overview.isStreaming ? "running" : "completed"}
            body={model.trajectory.overview.text}
            badges={model.trajectory.overview.runtimeBadges}
            defaultOpen
            isLast={model.trajectory.items.length === 0 && model.execution.sections.length === 0}
          />
        ) : null}
        {model.trajectory.items.map((item, index) => (
          <TrajectoryChainStep
            key={item.id}
            title={item.title}
            description={item.source === "cot" ? "Chain of thought" : "Reasoning step"}
            status={item.status}
            body={item.body}
            details={item.details}
            badges={item.runtimeBadges}
            defaultOpen={index === 0 && !model.trajectory.overview}
            isLast={
              index === model.trajectory.items.length - 1 && model.execution.sections.length === 0
            }
          />
        ))}
        {model.execution.sections.map((section, index) => (
          <TrajectoryChainStep
            key={section.id}
            title={section.label}
            description={section.summary}
            status={executionSectionState(section)}
            kind="tool"
            body={undefined}
            badges={section.runtimeBadges}
            defaultOpen={model.trajectory.items.length === 0 && index === 0}
            isLast={index === model.execution.sections.length - 1}
          >
            {renderExecutionSectionDetails(section) ?? (
              <div className="text-muted-foreground typo-caption">No additional detail.</div>
            )}
          </TrajectoryChainStep>
        ))}
      </TrajectoryChain>
      <SandboxActivityPanel events={sandboxEvents} isRunning={isRunning} />
    </div>
  );
}

/**
 * Extract categorized sandbox activity events from the selected turn's trace
 * parts. Status notes carrying a ``sandboxCategory`` originate from the
 * Daytona log stream relay and are surfaced in the SandboxActivityPanel.
 */
function extractSandboxEvents(selectedTurn: AssistantTurn | null): SandboxActivityEvent[] {
  if (!selectedTurn) return [];
  const parts = selectedTurn.attachedTraceParts ?? [];
  const now = Date.now();
  let counter = 0;
  const events: SandboxActivityEvent[] = [];
  for (const entry of parts) {
    const part = entry?.part;
    if (!part || part.kind !== "status_note" || !part.sandboxCategory) continue;
    const category = String(part.sandboxCategory) as SandboxActivityEvent["category"];
    // Trace parts arrive in append order; synthesize a monotonically
    // increasing timestamp so the panel renders them in arrival order.
    events.push({
      id: `sb-${counter}`,
      category,
      message: part.text,
      details: part.sandboxDetails,
      timestamp: now + counter,
    });
    counter += 1;
  }
  return events;
}

export function LiveTurnTrajectoryFallback({
  selectedTurn,
  title,
  description,
}: {
  selectedTurn: AssistantTurn;
  title: string;
  description: string;
}) {
  return (
    <div className="h-full w-full max-w-full overflow-y-auto overflow-x-hidden">
      <div className="workspace-trajectory-content flex min-w-0 max-w-full flex-col gap-3 overflow-hidden p-3">
        <Alert className="min-w-0 max-w-full overflow-hidden">
          <TriangleAlert className="text-muted-foreground" />
          <AlertTitle className="typo-label">{title}</AlertTitle>
          <AlertDescription className="typo-caption wrap-break-word">
            {description}
          </AlertDescription>
        </Alert>
        <SelectedTurnTrajectory selectedTurn={selectedTurn} />
      </div>
    </div>
  );
}

export function TraceLoading({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center gap-2 text-muted-foreground typo-caption">
      <Loader2 className="size-4 animate-spin" />
      <span>{label}</span>
    </div>
  );
}

export function TraceErrorPanel({ title, error }: { title: string; error: unknown }) {
  return (
    <div className="p-3">
      <Alert variant="destructive">
        <TriangleAlert />
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>
          {error instanceof Error ? error.message : String(error)}
        </AlertDescription>
      </Alert>
    </div>
  );
}

function SessionTraceSummary({ traceDebug }: { traceDebug: SessionTraceDebugResponse }) {
  const summary = traceDebug.performance_summary;
  const performanceBadges = [
    summary.total_duration_ms != null
      ? `total ${formatTraceDurationMs(summary.total_duration_ms)}`
      : null,
    summary.llm_duration_ms ? `LLM ${formatTraceDurationMs(summary.llm_duration_ms)}` : null,
    summary.repl_duration_ms ? `REPL ${formatTraceDurationMs(summary.repl_duration_ms)}` : null,
    summary.tool_duration_ms ? `tools ${formatTraceDurationMs(summary.tool_duration_ms)}` : null,
    summary.total_tokens ? formatTraceTokens(summary.total_tokens) : null,
    summary.adapter_fallback_count ? `${summary.adapter_fallback_count} fallbacks` : null,
    summary.parse_error_count ? `${summary.parse_error_count} parse errors` : null,
  ].filter(Boolean) as string[];
  const selectedSkills = summary.selected_skills ?? [];

  return (
    <Card className={inspectorStyles.card.root}>
      <CardHeader className={inspectorStyles.card.header}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="typo-label font-medium text-foreground">Session trace</CardTitle>
            <CardDescription className="typo-caption">
              Chronological MLflow/debug spans for the open workspace session.
            </CardDescription>
          </div>
          <Badge variant="secondary" className={inspectorStyles.badge.meta}>
            {traceDebug.span_count} spans
          </Badge>
        </div>
      </CardHeader>
      <CardContent className={inspectorStyles.card.content}>
        <div className={inspectorStyles.badge.row}>
          <Badge variant="outline" className={inspectorStyles.badge.meta}>
            {traceDebug.resolved_from}
          </Badge>
          {traceDebug.trace_id ? (
            <Badge variant="secondary" className={inspectorStyles.badge.meta}>
              trace {traceDebug.trace_id}
            </Badge>
          ) : null}
          {traceDebug.client_request_id ? (
            <Badge variant="secondary" className={inspectorStyles.badge.meta}>
              request {traceDebug.client_request_id}
            </Badge>
          ) : null}
          {traceDebug.renderable_span_count > 0 ? (
            <Badge variant="secondary" className={inspectorStyles.badge.meta}>
              {traceDebug.renderable_span_count} renderable
            </Badge>
          ) : null}
          {performanceBadges.map((badge) => (
            <Badge key={badge} variant="secondary" className={inspectorStyles.badge.meta}>
              {badge}
            </Badge>
          ))}
          {selectedSkills.map((skill) => (
            <Badge key={skill} variant="outline" className={inspectorStyles.badge.meta}>
              skill {skill}
            </Badge>
          ))}
          {summary.slowest_llm_span?.duration_ms != null ? (
            <Badge variant="outline" className={inspectorStyles.badge.meta}>
              slowest {summary.slowest_llm_span.name}{" "}
              {formatTraceDurationMs(summary.slowest_llm_span.duration_ms)}
            </Badge>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function TraceSpanChainStep({
  span,
  index,
  isLast,
}: {
  span: SessionTraceDebugResponse["spans"][number];
  index: number;
  isLast: boolean;
}) {
  const status = getTraceSpanStatus(span);
  const duration = formatTraceDuration(span);
  const tokenLabel = formatTraceTokens(span.total_tokens);
  const outputSizeLabel = formatTraceOutputChars(span.output_chars);
  const badges = [
    traceSpanKind(span),
    span.tool_name ? `tool ${span.tool_name}` : null,
    span.mapped_component_type ? span.mapped_component_type : null,
    duration ?? null,
    tokenLabel ?? null,
    outputSizeLabel ? `output ${outputSizeLabel}` : null,
    span.retry_or_fallback_reason ?? null,
  ].filter(Boolean) as string[];

  return (
    <TrajectoryChainStep
      title={`${index + 1}. ${traceSpanLabel(span)}`}
      description={`${span.span_type ?? "span"} · ${span.span_id}`}
      status={status}
      kind={span.tool_name ? "tool" : "span"}
      badges={badges}
      defaultOpen={index === 0 || status === "failed"}
      isLast={isLast}
      error={status === "failed"}
    >
      <div className={inspectorStyles.card.contentStack}>
        <DetailBlock label="Rationale" value={span.rationale ?? undefined} />
        <DetailBlock
          label="Metrics"
          value={[
            duration ? `Duration: ${duration}` : null,
            tokenLabel ? `Tokens: ${tokenLabel}` : null,
            outputSizeLabel ? `Output: ${outputSizeLabel}` : null,
            span.retry_or_fallback_reason ? `Fallback: ${span.retry_or_fallback_reason}` : null,
          ]
            .filter(Boolean)
            .join("\n")}
        />
        <DetailBlock label="Input" value={span.input_preview ?? undefined} />
        <DetailBlock
          label="Output"
          value={span.output_preview ?? undefined}
          tone={status === "failed" ? "error" : "default"}
        />
      </div>
    </TrajectoryChainStep>
  );
}

export function TrajectoryTimeline({
  selectedTurn,
  traceState,
}: {
  selectedTurn: AssistantTurn | null;
  traceState: SessionTraceState;
}) {
  if (!traceState.traceSessionId) {
    return (
      <EmptyPanel
        title="No active session"
        description="Start or open a workspace session to inspect persisted trace events."
        icon={Brain}
        className="h-full"
      />
    );
  }

  if (!traceState.hasSessionContent) {
    return (
      <EmptyPanel
        title="No session events"
        description="Send a message or open a saved workspace conversation to inspect trajectories."
        icon={Brain}
        className="h-full"
      />
    );
  }

  if (traceState.traceDebugQuery.isLoading || traceState.traceDebugQuery.isFetching) {
    return <TraceLoading label="Loading trace spans..." />;
  }

  if (traceState.traceDebugQuery.isError) {
    if (selectedTurn && selectedTurnHasTimeline(selectedTurn)) {
      return (
        <LiveTurnTrajectoryFallback
          selectedTurn={selectedTurn}
          title="Trace unavailable"
          description="Rendering live transcript reasoning and tool events for this turn."
        />
      );
    }
    return <TraceErrorPanel title="Trace unavailable" error={traceState.traceDebugQuery.error} />;
  }

  const traceDebug = traceState.traceDebugQuery.data;
  const spans = sortTraceSpans(traceDebug?.spans ?? []);

  if (!traceDebug || spans.length === 0) {
    if (selectedTurn && selectedTurnHasTimeline(selectedTurn)) {
      return (
        <LiveTurnTrajectoryFallback
          selectedTurn={selectedTurn}
          title="Trace spans unavailable"
          description="Rendering live transcript reasoning and tool events because this trace has no debug spans."
        />
      );
    }
    return (
      <EmptyPanel
        title="No trace spans"
        description="The selected session trace exists, but it has no debug spans to display."
        icon={Brain}
        className="h-full"
      />
    );
  }

  return (
    <div className="h-full w-full max-w-full overflow-y-auto overflow-x-hidden">
      <div className="workspace-trajectory-content flex min-w-0 max-w-full flex-col gap-3 overflow-hidden p-3">
        <SessionTraceSummary traceDebug={traceDebug} />
        {traceState.tracesQuery.isError ? (
          <Alert className="min-w-0 max-w-full overflow-hidden">
            <TriangleAlert className="text-muted-foreground" />
            <AlertTitle className="typo-label">Trace list unavailable</AlertTitle>
            <AlertDescription className="typo-caption wrap-break-word">
              Rendering trace-debug spans directly for this runtime session.
            </AlertDescription>
          </Alert>
        ) : null}
        <TrajectoryChain>
          {spans.map((span, index) => (
            <TraceSpanChainStep
              key={span.span_id}
              span={span}
              index={index}
              isLast={index === spans.length - 1}
            />
          ))}
        </TrajectoryChain>
        <SelectedTurnTrajectory selectedTurn={selectedTurn} />
      </div>
    </div>
  );
}
