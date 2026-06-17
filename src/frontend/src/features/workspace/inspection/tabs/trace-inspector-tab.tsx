import { memo, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ExternalLink } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useRuntimeStatus } from "@/hooks/use-runtime-status";
import { sessionsEndpoints } from "@/lib/rlm-api/sessions";
import type { SessionTraceDebugSpan } from "@/lib/rlm-api/sessions";
import { buildMlflowTraceUrl } from "@/lib/mlflow/trace-url";
import { chatRenderPartToAgentToolPart } from "@/lib/workspace/agent-tool-parts";
import type { ChatMessage } from "@/lib/workspace/workspace-types";
import type { AssistantTurnDisplayItem } from "@/lib/workspace/chat-display-items";
import { InspectorTabPanel } from "../inspector-tab-panel";

type TranscriptComponentRow = {
  id: string;
  label: string;
  traceSource: "live" | "summary" | "trajectory" | "assistant";
  renderKind: SessionTraceDebugSpan["mapped_render_kind"];
  componentType: string;
  toolName?: string;
};

function normalizeToken(value: string | undefined | null): string {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

function spanMatchKey(span: SessionTraceDebugSpan): string {
  const identity =
    span.tool_name ??
    span.mapped_component_type ??
    (span.mapped_render_kind === "reasoning" ? "reasoning" : span.name);
  return `${span.mapped_render_kind}:${normalizeToken(identity)}`;
}

function componentMatchKey(component: TranscriptComponentRow): string {
  const identity =
    component.toolName ??
    (component.renderKind === "reasoning" ? "reasoning" : component.componentType);
  return `${component.renderKind}:${normalizeToken(identity)}`;
}

function buildTranscriptComponentRows(
  selectedTurn: AssistantTurnDisplayItem | null,
): TranscriptComponentRow[] {
  if (!selectedTurn) return [];
  const rows: TranscriptComponentRow[] = [];

  if (selectedTurn.message?.content.trim()) {
    rows.push({
      id: `${selectedTurn.turnId}:assistant`,
      label: "Assistant reply",
      traceSource: "assistant",
      renderKind: "assistant_text",
      componentType: "assistant-text",
    });
  }

  for (const reasoningItem of selectedTurn.reasoningItems) {
    rows.push({
      id: reasoningItem.key,
      label: reasoningItem.part.label ?? "Reasoning",
      traceSource: reasoningItem.message.traceSource ?? "summary",
      renderKind: "reasoning",
      componentType: "tool-Thinking",
    });
  }

  for (const toolSession of selectedTurn.attachedToolSessions) {
    for (const item of toolSession.items) {
      const agentPart = chatRenderPartToAgentToolPart(item.part, selectedTurn.turnId, 0);
      rows.push({
        id: item.key,
        label: item.toolName ?? item.part.kind,
        traceSource: item.traceSource ?? "summary",
        renderKind:
          item.part.kind === "sandbox"
            ? "sandbox"
            : item.part.kind === "status_note"
              ? "status_note"
              : "tool",
        componentType: agentPart?.type ?? "tool-Unknown",
        toolName: agentPart?.toolName ?? item.toolName,
      });
    }
  }

  for (const tracePart of selectedTurn.attachedTraceParts) {
    const agentPart = chatRenderPartToAgentToolPart(tracePart.part, tracePart.message.id, 0);
    if (!agentPart) continue;
    const label =
      tracePart.part.kind === "status_note"
        ? tracePart.part.text
        : tracePart.part.kind === "environment_variables"
          ? (tracePart.part.title ?? "Environment variables")
          : tracePart.part.kind === "tool" || tracePart.part.kind === "sandbox"
            ? tracePart.part.title
            : tracePart.part.kind === "sources"
              ? (tracePart.part.title ?? "Sources")
              : tracePart.part.kind === "attachments"
                ? "Attachments"
                : "Trace detail";
    rows.push({
      id: tracePart.key,
      label,
      traceSource: tracePart.message.traceSource ?? "summary",
      renderKind:
        tracePart.part.kind === "sandbox"
          ? "sandbox"
          : tracePart.part.kind === "status_note"
            ? "status_note"
            : "tool",
      componentType: agentPart.type,
      toolName: agentPart.toolName,
    });
  }

  return rows;
}

function buildCoverageSummary(
  spans: SessionTraceDebugSpan[],
  transcriptRows: TranscriptComponentRow[],
): {
  liveMatched: SessionTraceDebugSpan[];
  summaryOnly: SessionTraceDebugSpan[];
  missing: SessionTraceDebugSpan[];
} {
  const liveCounts = new Map<string, number>();
  const anyCounts = new Map<string, number>();

  for (const row of transcriptRows) {
    const key = componentMatchKey(row);
    anyCounts.set(key, (anyCounts.get(key) ?? 0) + 1);
    if (row.traceSource === "live") {
      liveCounts.set(key, (liveCounts.get(key) ?? 0) + 1);
    }
  }

  const liveMatched: SessionTraceDebugSpan[] = [];
  const summaryOnly: SessionTraceDebugSpan[] = [];
  const missing: SessionTraceDebugSpan[] = [];

  for (const span of spans) {
    if (span.mapped_render_kind === "non_rendered") continue;
    const key = spanMatchKey(span);
    const liveCount = liveCounts.get(key) ?? 0;
    if (liveCount > 0) {
      liveCounts.set(key, liveCount - 1);
      const anyCount = anyCounts.get(key) ?? 0;
      if (anyCount > 0) anyCounts.set(key, anyCount - 1);
      liveMatched.push(span);
      continue;
    }
    const anyCount = anyCounts.get(key) ?? 0;
    if (anyCount > 0) {
      anyCounts.set(key, anyCount - 1);
      summaryOnly.push(span);
      continue;
    }
    missing.push(span);
  }

  return { liveMatched, summaryOnly, missing };
}

function traceBadgeTone(traceSource: TranscriptComponentRow["traceSource"]) {
  switch (traceSource) {
    case "live":
      return "default" as const;
    case "summary":
      return "secondary" as const;
    case "trajectory":
      return "secondary" as const;
    default:
      return "outline" as const;
  }
}

function traceMetadataFromTurn(
  selectedTurn: AssistantTurnDisplayItem | null,
): ChatMessage["traceMetadata"] | undefined {
  return selectedTurn?.message?.traceMetadata;
}

function formatObservedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export const TraceInspectorTab = memo(function TraceInspectorTab({
  sessionId,
  selectedTurn,
  messages,
}: {
  sessionId: string | null | undefined;
  selectedTurn: AssistantTurnDisplayItem | null;
  messages: ChatMessage[];
}) {
  const runtimeStatus = useRuntimeStatus();
  const traceMetadata = useMemo(() => traceMetadataFromTurn(selectedTurn), [selectedTurn]);
  const transcriptRows = useMemo(() => buildTranscriptComponentRows(selectedTurn), [selectedTurn]);

  const tracesQuery = useQuery({
    queryKey: ["workspace", "session-traces", sessionId],
    enabled: Boolean(sessionId && messages.length > 0),
    queryFn: ({ signal }) => sessionsEndpoints.traces(String(sessionId), {}, signal),
  });

  const resolvedTraceId = traceMetadata?.mlflowTraceId ?? null;
  const resolvedClientRequestId = traceMetadata?.mlflowClientRequestId ?? null;

  const traceDebugQuery = useQuery({
    queryKey: [
      "workspace",
      "session-trace-debug",
      sessionId,
      resolvedTraceId,
      resolvedClientRequestId,
    ],
    enabled: Boolean(
      sessionId && messages.length > 0 && (resolvedTraceId || resolvedClientRequestId),
    ),
    queryFn: ({ signal }) =>
      sessionsEndpoints.traceDebug(
        String(sessionId),
        { traceId: resolvedTraceId, clientRequestId: resolvedClientRequestId },
        signal,
      ),
  });

  const coverage = useMemo(
    () => buildCoverageSummary(traceDebugQuery.data?.spans ?? [], transcriptRows),
    [traceDebugQuery.data?.spans, transcriptRows],
  );

  const mlflowTraceUrl =
    resolvedTraceId && runtimeStatus.data?.mlflow?.enabled !== false
      ? buildMlflowTraceUrl({
          trackingUri: runtimeStatus.data?.mlflow?.tracking_uri ?? "http://127.0.0.1:5001",
          experimentId:
            tracesQuery.data?.items?.find((item) => item.trace_id === resolvedTraceId)
              ?.experiment_id ?? runtimeStatus.data?.mlflow?.experiment_id,
          traceId: resolvedTraceId,
        })
      : null;

  return (
    <InspectorTabPanel value="trace">
      <div className="flex flex-col gap-3">
        <Card className="border-border-subtle/80 bg-card/80 shadow-none">
          <CardHeader className="gap-2">
            <CardTitle className="text-sm font-medium text-foreground">
              Session trace debug
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            {resolvedTraceId ? (
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="font-mono text-xs">
                  {resolvedTraceId}
                </Badge>
                {resolvedClientRequestId ? (
                  <Badge variant="outline" className="font-mono text-xs">
                    {resolvedClientRequestId}
                  </Badge>
                ) : null}
                {mlflowTraceUrl ? (
                  <a
                    href={mlflowTraceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                  >
                    Open in MLflow
                    <ExternalLink className="size-3" />
                  </a>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No MLflow trace id is attached to the selected turn yet.
              </p>
            )}

            {traceDebugQuery.data ? (
              <div className="flex flex-wrap gap-2">
                <Badge variant="secondary">
                  {traceDebugQuery.data.renderable_span_count} renderable spans
                </Badge>
                <Badge variant="secondary">{coverage.liveMatched.length} live matched</Badge>
                <Badge variant="secondary">{coverage.summaryOnly.length} summary-only</Badge>
                <Badge variant={coverage.missing.length > 0 ? "destructive" : "secondary"}>
                  {coverage.missing.length} missing
                </Badge>
                <Badge variant="outline">
                  {traceDebugQuery.data.non_rendered_span_count} non-rendered
                </Badge>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {traceDebugQuery.error ? (
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertTitle>Trace inspection unavailable</AlertTitle>
            <AlertDescription>
              {traceDebugQuery.error instanceof Error
                ? traceDebugQuery.error.message
                : "Failed to inspect the session trace."}
            </AlertDescription>
          </Alert>
        ) : null}

        <Card className="border-border-subtle/80 bg-card/80 shadow-none">
          <CardHeader className="gap-2">
            <CardTitle className="text-sm font-medium text-foreground">
              Transcript components
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {transcriptRows.length > 0 ? (
              transcriptRows.map((row) => (
                <div
                  key={row.id}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border-subtle/60 px-3 py-2"
                >
                  <Badge variant={traceBadgeTone(row.traceSource)}>{row.traceSource}</Badge>
                  <span className="font-medium text-foreground">{row.label}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {row.componentType}
                  </span>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                Select an assistant turn to inspect the rendered chat components for that turn.
              </p>
            )}
          </CardContent>
        </Card>

        <Card className="border-border-subtle/80 bg-card/80 shadow-none">
          <CardHeader className="gap-2">
            <CardTitle className="text-sm font-medium text-foreground">
              Session trace rows
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {tracesQuery.data?.items?.length ? (
              tracesQuery.data.items.map((item) => (
                <div
                  key={`${item.provider}-${item.trace_id}`}
                  className="rounded-md border border-border-subtle/60 px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">{item.provider}</Badge>
                    <span className="font-mono text-xs text-foreground">{item.trace_id}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {formatObservedAt(item.observed_at)}
                  </p>
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                No persisted session trace rows were returned for this session.
              </p>
            )}
          </CardContent>
        </Card>

        {traceDebugQuery.data ? (
          <Card className="border-border-subtle/80 bg-card/80 shadow-none">
            <CardHeader className="gap-2">
              <CardTitle className="text-sm font-medium text-foreground">
                MLflow span mapping
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <ScrollArea className="max-h-96">
                <div className="flex flex-col gap-0">
                  {traceDebugQuery.data.spans.map((span, index) => (
                    <div key={span.span_id} className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge
                          variant={
                            span.mapped_render_kind === "non_rendered" ? "outline" : "secondary"
                          }
                        >
                          {span.mapped_render_kind}
                        </Badge>
                        <span className="font-medium text-foreground">{span.name}</span>
                        {span.mapped_component_type ? (
                          <span className="font-mono text-xs text-muted-foreground">
                            {span.mapped_component_type}
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{span.rationale}</p>
                      {span.input_preview ? (
                        <p className="mt-2 font-mono typo-body-xs text-muted-foreground">
                          in: {span.input_preview}
                        </p>
                      ) : null}
                      {span.output_preview ? (
                        <p className="mt-1 font-mono typo-body-xs text-muted-foreground">
                          out: {span.output_preview}
                        </p>
                      ) : null}
                      {index < traceDebugQuery.data.spans.length - 1 ? (
                        <Separator className="mt-3 bg-border-subtle/60" />
                      ) : null}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        ) : null}

        {traceDebugQuery.data && coverage.missing.length > 0 ? (
          <Alert>
            <AlertTitle>Unmatched renderable spans</AlertTitle>
            <AlertDescription>
              <div className="mt-2 flex flex-col gap-2">
                {coverage.missing.map((span) => (
                  <div
                    key={span.span_id}
                    className="rounded-md border border-border-subtle/60 px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="destructive">{span.mapped_render_kind}</Badge>
                      <span className="font-medium text-foreground">{span.name}</span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{span.rationale}</p>
                  </div>
                ))}
              </div>
            </AlertDescription>
          </Alert>
        ) : null}
      </div>
    </InspectorTabPanel>
  );
});
