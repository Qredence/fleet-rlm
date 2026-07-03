import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vite-plus/test";

import type { AssistantTurnDisplayItem } from "@/lib/workspace/chat-display-items";
import type { SessionTraceDebugResponse } from "@/lib/rlm-api/sessions";
import type { ChatMessage, ChatRenderPart } from "@/lib/workspace/workspace-types";
import { useChatStore } from "@/features/workspace/use-workspace";
import { useWorkspaceUiStore } from "@/lib/workspace/workspace-ui-store";

import type { SessionTraceState } from "../../use-session-trace";
import { GraphTab } from "../graph-tab";
import { TrajectoryTimeline } from "../trajectory-tab";

function makeAssistantTurn(renderParts: ChatRenderPart[]): AssistantTurnDisplayItem {
  const message: ChatMessage = {
    id: "assistant-message",
    type: "assistant",
    content: "Done",
    phase: 1,
    renderParts,
  };

  return {
    kind: "assistant_turn",
    key: "assistant-turn",
    turnId: "assistant-message",
    message,
    isPendingShell: false,
    reasoningItems: [],
    trajectoryItems: [],
    attachedToolSessions: [],
    attachedTraceParts: [],
  };
}

function makeEmptyTraceDebug(): SessionTraceDebugResponse {
  return {
    trace_id: "trace-empty",
    resolved_from: "trace_id",
    span_count: 0,
    renderable_span_count: 0,
    non_rendered_span_count: 0,
    performance_summary: {
      total_duration_ms: null,
      llm_duration_ms: 0,
      repl_duration_ms: 0,
      tool_duration_ms: 0,
      root_overhead_ms: null,
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
      token_total_mismatch: false,
      adapter_fallback_count: 0,
      parse_error_count: 0,
      selected_skills: [],
      rlm_action_max_tokens: null,
      rlm_max_output_chars: null,
      slowest_llm_span: null,
      largest_output_span: null,
    },
    spans: [],
  };
}

function makePerformanceTraceDebug(): SessionTraceDebugResponse {
  return {
    trace_id: "trace-performance",
    resolved_from: "trace_id",
    span_count: 1,
    renderable_span_count: 1,
    non_rendered_span_count: 0,
    performance_summary: {
      total_duration_ms: 12_500,
      llm_duration_ms: 8_000,
      repl_duration_ms: 2_000,
      tool_duration_ms: 500,
      root_overhead_ms: 2_000,
      input_tokens: 10_000,
      output_tokens: 1_250,
      total_tokens: 11_250,
      token_total_mismatch: false,
      adapter_fallback_count: 2,
      parse_error_count: 1,
      selected_skills: ["long-context", "rlm"],
      rlm_action_max_tokens: 4096,
      rlm_max_output_chars: 5000,
      slowest_llm_span: {
        span_id: "span-llm",
        name: "LM.__call__",
        duration_ms: 8000,
        input_tokens: 10000,
        output_tokens: 1250,
        total_tokens: 11250,
        output_chars: 48000,
      },
      largest_output_span: null,
    },
    spans: [
      {
        span_id: "span-llm",
        parent_span_id: null,
        name: "LM.__call__",
        span_type: "LLM",
        status_code: "OK",
        tool_name: null,
        mapped_render_kind: "reasoning",
        mapped_component_type: null,
        rationale: "renderable span",
        input_preview: "large prompt",
        output_preview: "large output",
        start_time_unix_nano: "0",
        end_time_unix_nano: "8000000000",
        duration_ms: 8000,
        input_tokens: 10000,
        output_tokens: 1250,
        total_tokens: 11250,
        output_chars: 48000,
        retry_or_fallback_reason: "adapter_parse_error",
      },
    ],
  };
}

function makeTraceState(): SessionTraceState {
  return {
    traceSessionId: "session-1",
    hasSessionContent: true,
    traceDebugQuery: {
      data: makeEmptyTraceDebug(),
      isLoading: false,
      isFetching: false,
      isError: false,
    },
    tracesQuery: {
      isError: false,
    },
  } as SessionTraceState;
}

const liveExecutionParts: ChatRenderPart[] = [
  {
    kind: "reasoning",
    parts: [{ type: "text", text: "Inspect the workspace state." }],
    isStreaming: false,
  },
  {
    kind: "sandbox",
    title: "repl_execute",
    state: "output-available",
    code: "print('hello')",
    output: "hello",
    language: "python",
  },
];

describe("sidepanel empty trace fallbacks", () => {
  beforeEach(() => {
    useChatStore.setState({
      messages: [],
      turnArtifactsByMessageId: {},
      isStreaming: false,
      sessionId: "session-1",
      runtimeSessionId: "session-1",
      durableSessionId: null,
    });
    useWorkspaceUiStore.setState({
      selectedAssistantTurnId: null,
    });
  });

  it("renders selected turn trajectory content when trace debug has no spans", () => {
    const html = renderToStaticMarkup(
      <TrajectoryTimeline
        selectedTurn={makeAssistantTurn(liveExecutionParts)}
        traceState={makeTraceState()}
      />,
    );

    expect(html).toContain("repl_execute");
    expect(html).toContain("hello");
    expect(html).not.toContain("No trace spans");
  });

  it("renders trace performance summary and span metrics when available", () => {
    const traceState = {
      ...makeTraceState(),
      traceDebugQuery: {
        data: makePerformanceTraceDebug(),
        isLoading: false,
        isFetching: false,
        isError: false,
      },
    } as SessionTraceState;

    const html = renderToStaticMarkup(
      <TrajectoryTimeline selectedTurn={makeAssistantTurn([])} traceState={traceState} />,
    );

    expect(html).toContain("total 13s");
    expect(html).toContain("LLM 8.0s");
    expect(html).toContain("REPL 2.0s");
    expect(html).toContain("11,250 tokens");
    expect(html).toContain("2 fallbacks");
    expect(html).toContain("skill long-context");
    expect(html).toContain("skill rlm");
    expect(html).toContain("LM.__call__");
    expect(html).toContain("48,000 chars");
    expect(html).toContain("adapter_parse_error");
  });

  it("renders live turn content in the graph tab when spans and artifact graph are empty", () => {
    useChatStore.setState({
      messages: [
        {
          id: "user-message",
          type: "user",
          content: "hi",
        },
        {
          id: "trace-reasoning",
          type: "trace",
          content: "reasoning",
          traceSource: "live",
          renderParts: [liveExecutionParts[0]!],
        },
        {
          id: "trace-sandbox",
          type: "trace",
          content: "sandbox",
          traceSource: "live",
          renderParts: [liveExecutionParts[1]!],
        },
        {
          id: "assistant-message",
          type: "assistant",
          content: "Done",
          phase: 1,
        },
      ],
      turnArtifactsByMessageId: {},
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<GraphTab traceState={makeTraceState()} />);
    });

    const html = container.textContent ?? "";

    expect(html).toContain("Sandbox Execution");
    expect(html).toContain("hello");
    expect(html).toContain("print('hello')");
    expect(html).not.toContain("No graph spans");

    act(() => root.unmount());
  });
});
