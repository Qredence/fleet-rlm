import type { Edge, Node } from "@xyflow/react";

import type { SessionTraceDebugSpan, SessionTraceItem } from "@/lib/rlm-api/sessions";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

export type SessionTraceTargetSource = "metadata" | "session-row" | "none";

export interface SessionTraceTarget {
  traceId: string | null;
  clientRequestId: string | null;
  source: SessionTraceTargetSource;
  traceRow?: SessionTraceItem;
}

export interface WorkspaceTraceSessionScope {
  sessionId: string | null;
  source: "durable" | "runtime" | "none";
}

export type TraceSpanStatus = "running" | "completed" | "failed" | "unknown";

export interface TraceSpanNodeData extends Record<string, unknown> {
  span: SessionTraceDebugSpan;
  label: string;
  kind: string;
  status: TraceSpanStatus;
  toolName?: string;
  componentType?: string;
  durationLabel?: string;
  tokenLabel?: string;
  outputSizeLabel?: string;
  fallbackReason?: string;
  summary?: string;
}

const TRACE_COLUMN_WIDTH = 320;
const TRACE_ROW_HEIGHT = 142;

function trimToken(value: string | null | undefined): string | null {
  const trimmed = String(value ?? "").trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function latestAssistantTraceMetadata(
  messages: ChatMessage[],
): ChatMessage["traceMetadata"] | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.type === "assistant" && message.traceMetadata) {
      return message.traceMetadata;
    }
  }
  return undefined;
}

export function resolveSessionTraceTarget(
  messages: ChatMessage[],
  traces: SessionTraceItem[],
): SessionTraceTarget {
  const metadata = latestAssistantTraceMetadata(messages);
  const metadataTraceId = trimToken(metadata?.mlflowTraceId);
  const metadataClientRequestId = trimToken(metadata?.mlflowClientRequestId);

  if (metadataTraceId || metadataClientRequestId) {
    return {
      traceId: metadataTraceId,
      clientRequestId: metadataClientRequestId,
      source: "metadata",
    };
  }

  const latestTrace = traces[0];
  if (latestTrace) {
    return {
      traceId: trimToken(latestTrace.trace_id),
      clientRequestId: trimToken(latestTrace.client_request_id),
      source: "session-row",
      traceRow: latestTrace,
    };
  }

  return { traceId: null, clientRequestId: null, source: "none" };
}

export function resolveWorkspaceTraceSessionScope(input: {
  durableSessionId?: string | null;
  runtimeSessionId?: string | null;
  legacySessionId?: string | null;
}): WorkspaceTraceSessionScope {
  const durableSessionId = trimToken(input.durableSessionId);
  if (durableSessionId) return { sessionId: durableSessionId, source: "durable" };

  const runtimeSessionId = trimToken(input.runtimeSessionId) ?? trimToken(input.legacySessionId);
  if (runtimeSessionId) return { sessionId: runtimeSessionId, source: "runtime" };

  return { sessionId: null, source: "none" };
}

function spanStart(span: SessionTraceDebugSpan): number {
  return Number(span.start_time_unix_nano ?? 0);
}

function spanEnd(span: SessionTraceDebugSpan): number {
  return Number(span.end_time_unix_nano ?? 0);
}

export function sortTraceSpans(spans: SessionTraceDebugSpan[]): SessionTraceDebugSpan[] {
  return [...spans].sort((a, b) => {
    const aStart = spanStart(a);
    const bStart = spanStart(b);
    if (aStart !== bStart) return aStart - bStart;
    return String(a.span_id).localeCompare(String(b.span_id));
  });
}

export function getTraceSpanStatus(span: SessionTraceDebugSpan): TraceSpanStatus {
  const status = String(span.status_code ?? "")
    .trim()
    .toLowerCase();
  if (/error|fail|exception|timeout/.test(status)) return "failed";
  if (/ok|success|completed|complete|finished/.test(status)) return "completed";
  if (span.start_time_unix_nano && !span.end_time_unix_nano) return "running";
  if (span.end_time_unix_nano) return "completed";
  return "unknown";
}

export function formatTraceDuration(span: SessionTraceDebugSpan): string | undefined {
  if (typeof span.duration_ms === "number" && Number.isFinite(span.duration_ms)) {
    return formatTraceDurationMs(span.duration_ms);
  }
  const start = spanStart(span);
  const end = spanEnd(span);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0 || end <= start) {
    return undefined;
  }
  return formatTraceDurationMs((end - start) / 1_000_000);
}

export function formatTraceDurationMs(milliseconds: number): string | undefined {
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
  const seconds = milliseconds / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

export function formatTraceCount(value: number | null | undefined): string | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatTraceTokens(value: number | null | undefined): string | undefined {
  const formatted = formatTraceCount(value);
  return formatted ? `${formatted} tokens` : undefined;
}

export function formatTraceOutputChars(value: number | null | undefined): string | undefined {
  const formatted = formatTraceCount(value);
  return formatted ? `${formatted} chars` : undefined;
}

export function traceSpanLabel(span: SessionTraceDebugSpan): string {
  return (
    trimToken(span.tool_name) ??
    trimToken(span.mapped_component_type) ??
    trimToken(span.name) ??
    trimToken(span.span_id) ??
    "Trace span"
  );
}

export function traceSpanKind(span: SessionTraceDebugSpan): string {
  return (
    trimToken(span.mapped_render_kind) ??
    trimToken(span.span_type) ??
    trimToken(span.mapped_component_type) ??
    "span"
  );
}

function traceSpanSummary(span: SessionTraceDebugSpan): string | undefined {
  return (
    trimToken(span.rationale) ??
    trimToken(span.output_preview) ??
    trimToken(span.input_preview) ??
    undefined
  );
}

function buildDepthMap(ordered: SessionTraceDebugSpan[]): Map<string, number> {
  const byId = new Map(ordered.map((span) => [String(span.span_id), span]));
  const depthById = new Map<string, number>();

  function depthFor(span: SessionTraceDebugSpan, seen = new Set<string>()): number {
    const spanId = String(span.span_id);
    const cached = depthById.get(spanId);
    if (cached != null) return cached;

    const parentId = trimToken(span.parent_span_id);
    if (!parentId || seen.has(parentId)) {
      depthById.set(spanId, 0);
      return 0;
    }

    const parent = byId.get(parentId);
    if (!parent) {
      depthById.set(spanId, 0);
      return 0;
    }

    seen.add(spanId);
    const depth = depthFor(parent, seen) + 1;
    depthById.set(spanId, depth);
    return depth;
  }

  for (const span of ordered) {
    depthFor(span);
  }

  return depthById;
}

export function buildTraceFlowGraph(spans: SessionTraceDebugSpan[]): {
  nodes: Node<TraceSpanNodeData>[];
  edges: Edge[];
} {
  const ordered = sortTraceSpans(spans);
  const spanIds = new Set(ordered.map((span) => String(span.span_id)));
  const depthById = buildDepthMap(ordered);

  const nodes: Node<TraceSpanNodeData>[] = ordered.map((span, index) => {
    const spanId = String(span.span_id);
    const depth = depthById.get(spanId) ?? 0;
    return {
      id: spanId,
      type: "traceSpan",
      position: {
        x: depth * TRACE_COLUMN_WIDTH,
        y: index * TRACE_ROW_HEIGHT,
      },
      data: {
        span,
        label: traceSpanLabel(span),
        kind: traceSpanKind(span),
        status: getTraceSpanStatus(span),
        toolName: trimToken(span.tool_name) ?? undefined,
        componentType: trimToken(span.mapped_component_type) ?? undefined,
        durationLabel: formatTraceDuration(span),
        tokenLabel: formatTraceTokens(span.total_tokens),
        outputSizeLabel: formatTraceOutputChars(span.output_chars),
        fallbackReason: trimToken(span.retry_or_fallback_reason) ?? undefined,
        summary: traceSpanSummary(span),
      },
    };
  });

  const edges: Edge[] = [];
  const connectedTargets = new Set<string>();

  for (const span of ordered) {
    const spanId = String(span.span_id);
    const parentId = trimToken(span.parent_span_id);
    if (!parentId || !spanIds.has(parentId)) continue;
    connectedTargets.add(spanId);
    edges.push({
      id: `parent-${parentId}-${spanId}`,
      source: parentId,
      target: spanId,
      type: "smoothstep",
    });
  }

  for (let index = 1; index < ordered.length; index += 1) {
    const previousId = String(ordered[index - 1]!.span_id);
    const currentId = String(ordered[index]!.span_id);
    if (connectedTargets.has(currentId)) continue;
    edges.push({
      id: `chrono-${previousId}-${currentId}`,
      source: previousId,
      target: currentId,
      type: "smoothstep",
      animated: false,
      className: "trace-flow-edge-fallback",
      data: { fallback: true },
    });
  }

  return { nodes, edges };
}
