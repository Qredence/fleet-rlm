import { describe, expect, it } from "vite-plus/test";

import {
  buildTraceFlowGraph,
  resolveSessionTraceTarget,
  resolveWorkspaceTraceSessionScope,
  sortTraceSpans,
} from "@/features/workspace/screen/workspace-session-trace-model";
import type { SessionTraceDebugSpan, SessionTraceItem } from "@/lib/rlm-api/sessions";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

function span(input: Partial<SessionTraceDebugSpan> & { span_id: string }): SessionTraceDebugSpan {
  return {
    span_id: input.span_id,
    parent_span_id: input.parent_span_id ?? null,
    name: input.name ?? input.span_id,
    span_type: input.span_type ?? "CHAIN",
    status_code: input.status_code ?? "OK",
    tool_name: input.tool_name ?? null,
    mapped_render_kind: input.mapped_render_kind ?? "tool",
    mapped_component_type: input.mapped_component_type ?? null,
    rationale: input.rationale ?? "renderable span",
    input_preview: input.input_preview ?? null,
    output_preview: input.output_preview ?? null,
    start_time_unix_nano: input.start_time_unix_nano ?? null,
    end_time_unix_nano: input.end_time_unix_nano ?? null,
  };
}

describe("workspace session trace model", () => {
  it("prefers latest assistant trace metadata over persisted session rows", () => {
    const messages: ChatMessage[] = [
      {
        id: "user-1",
        type: "user",
        content: "hello",
      },
      {
        id: "assistant-1",
        type: "assistant",
        content: "older",
        traceMetadata: {
          mlflowTraceId: "trace-old",
          mlflowClientRequestId: "request-old",
        },
      },
      {
        id: "assistant-2",
        type: "assistant",
        content: "latest",
        traceMetadata: {
          mlflowTraceId: "trace-latest",
          mlflowClientRequestId: "request-latest",
        },
      },
    ];
    const traces = [
      {
        trace_id: "trace-row",
        client_request_id: "request-row",
      } as SessionTraceItem,
    ];

    expect(resolveSessionTraceTarget(messages, traces)).toMatchObject({
      traceId: "trace-latest",
      clientRequestId: "request-latest",
      source: "metadata",
    });
  });

  it("falls back to the latest session trace row when messages have no trace metadata", () => {
    const traces = [
      {
        trace_id: "trace-row",
        client_request_id: "request-row",
      } as SessionTraceItem,
    ];

    expect(resolveSessionTraceTarget([], traces)).toMatchObject({
      traceId: "trace-row",
      clientRequestId: "request-row",
      source: "session-row",
    });
  });

  it("prefers durable trace scope over runtime and legacy ids", () => {
    expect(
      resolveWorkspaceTraceSessionScope({
        durableSessionId: "durable-session",
        runtimeSessionId: "runtime-session",
        legacySessionId: "legacy-session",
      }),
    ).toEqual({ sessionId: "durable-session", source: "durable" });
  });

  it("uses runtime trace scope when no durable id is available", () => {
    expect(
      resolveWorkspaceTraceSessionScope({
        durableSessionId: null,
        runtimeSessionId: "runtime-session",
        legacySessionId: "legacy-session",
      }),
    ).toEqual({ sessionId: "runtime-session", source: "runtime" });
  });

  it("sorts spans chronologically before rendering timeline and graph", () => {
    const ordered = sortTraceSpans([
      span({ span_id: "late", start_time_unix_nano: "300" }),
      span({ span_id: "early", start_time_unix_nano: "100" }),
      span({ span_id: "middle", start_time_unix_nano: "200" }),
    ]);

    expect(ordered.map((item) => item.span_id)).toEqual(["early", "middle", "late"]);
  });

  it("maps parent span ids and parentless spans into React Flow nodes and edges", () => {
    const graph = buildTraceFlowGraph([
      span({ span_id: "root", start_time_unix_nano: "100", name: "Root" }),
      span({
        span_id: "tool",
        parent_span_id: "root",
        start_time_unix_nano: "200",
        tool_name: "repo_search",
      }),
      span({ span_id: "orphan", start_time_unix_nano: "300", name: "Orphan" }),
    ]);

    expect(graph.nodes.map((node) => node.id)).toEqual(["root", "tool", "orphan"]);
    expect(graph.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "parent-root-tool", source: "root", target: "tool" }),
        expect.objectContaining({ id: "chrono-tool-orphan", source: "tool", target: "orphan" }),
      ]),
    );
    expect(graph.nodes.find((node) => node.id === "tool")?.data.label).toBe("repo_search");
  });
});
