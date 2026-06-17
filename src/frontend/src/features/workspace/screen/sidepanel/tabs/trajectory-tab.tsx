import { useMemo } from "react";
import { Brain, Loader2, TriangleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyPanel } from "@/components/product/empty-panel";
import { buildAssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
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
  getTraceSpanStatus,
  sortTraceSpans,
  traceSpanKind,
  traceSpanLabel,
} from "@/features/workspace/screen/workspace-session-trace-model";
import { type SessionTraceDebugResponse } from "@/lib/rlm-api/sessions";
import { cn } from "@/lib/utils";
import { buildChatDisplayItems } from "@/lib/workspace/chat-display-items";

import { type SessionTraceState, selectedTurnStatus, traceStatusTone } from "../use-session-trace";

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
const trajectoryContentStackClass = cn(
  inspectorStyles.card.contentStack,
  "min-w-0 max-w-full overflow-hidden",
);

function SelectedTurnTrajectory({ selectedTurn }: { selectedTurn: AssistantTurn | null }) {
  const model = useMemo(
    () => (selectedTurn ? buildAssistantContentModel(selectedTurn) : null),
    [selectedTurn],
  );

  if (!selectedTurn || !model) return null;

  const status = selectedTurnStatus(model);
  const tone = statusTone(status);
  const hasTimeline =
    model.trajectory.hasContent || model.execution.hasContent || model.answer.hasContent;

  if (!hasTimeline) return null;

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

      {model.trajectory.overview ? (
        <Card className={trajectoryCardClass}>
          <CardHeader className={trajectoryHeaderClass}>
            <div className="flex min-w-0 items-center gap-2">
              <Brain className="size-4 text-muted-foreground" />
              <CardTitle className="typo-label font-medium text-foreground">Thinking</CardTitle>
            </div>
            {renderBadges(model.trajectory.overview.runtimeBadges)}
          </CardHeader>
          <CardContent className={trajectoryContentStackClass}>
            <DetailBlock label="Reasoning" value={model.trajectory.overview.text} />
          </CardContent>
        </Card>
      ) : null}

      {model.trajectory.items.map((item) => {
        const itemTone = statusTone(item.status);
        return (
          <Card key={item.id} className={trajectoryCardClass}>
            <CardHeader className={trajectoryHeaderClass}>
              <div className="flex min-w-0 items-start justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle className="typo-label font-medium text-foreground">
                    {item.title}
                  </CardTitle>
                  <CardDescription className="max-w-full typo-caption wrap-break-word">
                    {item.source === "cot" ? "Chain of thought" : "Reasoning step"}
                  </CardDescription>
                </div>
                <Badge variant={itemTone.variant} className={inspectorStyles.badge.status}>
                  {itemTone.label}
                </Badge>
              </div>
              {renderBadges(item.runtimeBadges)}
            </CardHeader>
            <CardContent className={trajectoryContentStackClass}>
              <DetailBlock label="Detail" value={item.body} />
            </CardContent>
          </Card>
        );
      })}

      {model.execution.sections.map((section) => {
        const sectionTone = statusTone(executionSectionState(section));
        return (
          <Card key={section.id} className={trajectoryCardClass}>
            <CardHeader className={trajectoryHeaderClass}>
              <div className="flex min-w-0 items-start justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle className="typo-label font-medium text-foreground">
                    {section.label}
                  </CardTitle>
                  <CardDescription className="max-w-full typo-caption wrap-break-word">
                    {section.summary}
                  </CardDescription>
                </div>
                <Badge variant={sectionTone.variant} className={inspectorStyles.badge.status}>
                  {sectionTone.label}
                </Badge>
              </div>
              {renderBadges(section.runtimeBadges)}
            </CardHeader>
            <CardContent className={trajectoryContentStackClass}>
              {renderExecutionSectionDetails(section) ?? (
                <div className="text-muted-foreground typo-caption">No additional detail.</div>
              )}
            </CardContent>
          </Card>
        );
      })}
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
        </div>
      </CardContent>
    </Card>
  );
}

function TraceSpanCard({
  span,
  index,
}: {
  span: SessionTraceDebugResponse["spans"][number];
  index: number;
}) {
  const status = getTraceSpanStatus(span);
  const tone = traceStatusTone(status);
  const duration = formatTraceDuration(span);
  const badges = [
    traceSpanKind(span),
    span.tool_name ? `tool ${span.tool_name}` : null,
    span.mapped_component_type ? span.mapped_component_type : null,
    duration ?? null,
  ].filter(Boolean) as string[];

  return (
    <Card className={inspectorStyles.card.root}>
      <CardHeader className={inspectorStyles.card.header}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="truncate typo-label font-medium text-foreground">
              {index + 1}. {traceSpanLabel(span)}
            </CardTitle>
            <CardDescription className="truncate typo-caption">
              {span.span_type ?? "span"} · {span.span_id}
            </CardDescription>
          </div>
          <Badge variant={tone.variant} className={inspectorStyles.badge.status}>
            {tone.label}
          </Badge>
        </div>
        {renderBadges(badges, "secondary")}
      </CardHeader>
      <CardContent className={inspectorStyles.card.contentStack}>
        <DetailBlock label="Rationale" value={span.rationale ?? undefined} />
        <DetailBlock label="Input" value={span.input_preview ?? undefined} />
        <DetailBlock
          label="Output"
          value={span.output_preview ?? undefined}
          tone={status === "failed" ? "error" : "default"}
        />
      </CardContent>
    </Card>
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
    if (selectedTurn) {
      return (
        <div className="h-full w-full max-w-full overflow-y-auto overflow-x-hidden">
          <div className="workspace-trajectory-content flex min-w-0 max-w-full flex-col gap-3 overflow-hidden p-3">
            <Alert className="min-w-0 max-w-full overflow-hidden">
              <TriangleAlert className="text-muted-foreground" />
              <AlertTitle className="typo-label">Trace unavailable</AlertTitle>
              <AlertDescription className="typo-caption wrap-break-word">
                Rendering live transcript reasoning and tool events for this turn.
              </AlertDescription>
            </Alert>
            <SelectedTurnTrajectory selectedTurn={selectedTurn} />
          </div>
        </div>
      );
    }
    return <TraceErrorPanel title="Trace unavailable" error={traceState.traceDebugQuery.error} />;
  }

  const traceDebug = traceState.traceDebugQuery.data;
  const spans = sortTraceSpans(traceDebug?.spans ?? []);

  if (!traceDebug || spans.length === 0) {
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
        {spans.map((span, index) => (
          <TraceSpanCard key={span.span_id} span={span} index={index} />
        ))}
        <SelectedTurnTrajectory selectedTurn={selectedTurn} />
      </div>
    </div>
  );
}
