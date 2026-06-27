import { describe, expect, it } from "vitest";
import { applyWsFrameToMessages } from "@/lib/workspace/backend-chat-event-adapter";
import type { WsServerMessage } from "@/lib/rlm-api";

describe("backend chat event adapter contract", () => {
  it("renders canonical execution start events with runtime payloads", () => {
    const frame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_started",
        text: "Preparing Daytona workspace...",
        payload: {
          phase: "startup",
          runtime: {
            runtime_mode: "daytona_pilot",
            execution_mode: "auto",
            sandbox_id: "sbx-123",
            volume_name: "workspace-a",
            workspace_path: "/workspace",
            depth: 0,
            max_depth: 2,
          },
        },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-1",
      },
    };

    const result = applyWsFrameToMessages([], frame);

    expect(result.terminal).toBe(false);
    expect(result.errored).toBe(false);
    expect(result.messages).toHaveLength(1);
    expect(result.messages[0]?.type).toBe("trace");
    expect(result.messages[0]?.renderParts?.[0]).toMatchObject({
      kind: "status_note",
      text: "Preparing Daytona workspace...",
    });
  });

  it("round-trips trajectory tool_call frames into trace render parts", () => {
    const frame: WsServerMessage = {
      type: "event",
      data: {
        kind: "tool_call",
        text: "read_file",
        payload: {
          tool_name: "read_file",
          tool_args: { path: "/workspace/README.md" },
          step_index: 0,
        },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-tool-1",
      },
    };

    const result = applyWsFrameToMessages(
      [
        {
          id: "assistant-1",
          type: "assistant",
          content: "",
          streaming: true,
          renderParts: [],
        },
      ],
      frame,
    );

    const traceMessage = result.messages.find((message) => message.type === "trace");
    expect(traceMessage?.renderParts?.some((part) => part.kind === "tool")).toBe(true);
  });

  it("routes production execution_step frames by source_type without payload.step", () => {
    const runtime = {
      runtime_mode: "daytona_pilot",
      execution_mode: "auto",
      depth: 0,
      max_depth: 2,
    };

    const reasoningFrame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_step",
        text: "I should inspect the repo layout first.",
        payload: { source_type: "reasoning", runtime },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-reason-1",
      },
    };

    const afterReasoning = applyWsFrameToMessages([], reasoningFrame);
    expect(afterReasoning.messages).toHaveLength(1);
    expect(afterReasoning.messages[0]?.type).toBe("trace");
    // P2-5: reasoning source_type now routes to compact status traces
    // instead of full reasoning blocks in the main chat.
    expect(afterReasoning.messages[0]?.renderParts?.[0]).toMatchObject({
      kind: "status_note",
      text: "I should inspect the repo layout first.",
    });

    const toolCallFrame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_step",
        text: "read_file",
        payload: {
          source_type: "tool_call",
          tool_name: "read_file",
          tool_args: { path: "/workspace/README.md" },
          runtime,
        },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-tool-call-1",
      },
    };

    const afterToolCall = applyWsFrameToMessages(afterReasoning.messages, toolCallFrame);
    const traceAfterTool = afterToolCall.messages.find((message) => message.type === "trace");
    expect(traceAfterTool?.renderParts).toHaveLength(2);
    // P2-5: reasoning is now rendered as status_note
    expect(traceAfterTool?.renderParts?.[0]?.kind).toBe("status_note");
    expect(traceAfterTool?.renderParts?.[1]?.kind).toBe("tool");

    const toolResultFrame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_step",
        text: "README contents",
        payload: {
          source_type: "tool_result",
          tool_name: "read_file",
          tool_output: "# Fleet RLM",
          runtime,
        },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-tool-result-1",
      },
    };

    const afterToolResult = applyWsFrameToMessages(afterToolCall.messages, toolResultFrame);
    const traceAfterResult = afterToolResult.messages.find((message) => message.type === "trace");
    expect(traceAfterResult?.renderParts).toHaveLength(3);
    // P2-5: reasoning now renders as status_note
    expect(traceAfterResult?.renderParts?.map((part) => part.kind)).toEqual([
      "status_note",
      "tool",
      "tool",
    ]);
  });

  it("appends assistant tokens from execution_step source_type text frames", () => {
    const frame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_step",
        text: "Hello",
        payload: { source_type: "text" },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-text-1",
      },
    };

    const result = applyWsFrameToMessages(
      [{ id: "assistant-1", type: "assistant", content: "", streaming: true, renderParts: [] }],
      frame,
    );

    const assistant = result.messages.find((message) => message.type === "assistant");
    expect(assistant?.content).toBe("Hello");
    expect(assistant?.streaming).toBe(true);
  });

  it("routes execution emitter llm steps with reasoning phase to ThinkingTool parts", () => {
    const frame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_step",
        text: "The user wants a short arithmetic answer.",
        payload: {
          source_type: "execution_step",
          step: {
            type: "llm",
            label: "The user wants a short arithmetic answer.",
            input: { phase: "reasoning" },
            output: { text: "The user wants a short arithmetic answer." },
          },
        },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-reason-step-1",
      },
    };

    const result = applyWsFrameToMessages([], frame);
    const trace = result.messages.find((message) => message.type === "trace");
    const reasoningPart = trace?.renderParts?.find((part) => part.kind === "reasoning");

    expect(reasoningPart).toMatchObject({
      kind: "reasoning",
      isStreaming: true,
      parts: [{ text: "The user wants a short arithmetic answer." }],
    });
  });

  it("finishes streaming reasoning on execution_completed", () => {
    const runtime = { runtime_mode: "daytona_pilot" };
    const reasoningFrame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_step",
        text: "Planning response.",
        payload: { source_type: "reasoning", runtime },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-reason-2",
      },
    };

    const afterReasoning = applyWsFrameToMessages([], reasoningFrame);
    const completionFrame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_completed",
        text: "Done.",
        payload: {
          final_artifact: { text: "Done." },
          runtime,
          run_summary: { status: "completed" },
        },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-complete-1",
      },
    };

    const result = applyWsFrameToMessages(afterReasoning.messages, completionFrame);
    const trace = result.messages.find((message) => message.type === "trace");
    // P2-5: reasoning now routes to status_note, not reasoning kind.
    // Status notes don't have isStreaming — they're always complete.
    const reasoningPart = trace?.renderParts?.find((part) => part.kind === "status_note");
    expect(reasoningPart).toBeDefined();
  });

  it("renders canonical execution completion events", () => {
    const frame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_completed",
        text: "Final answer",
        payload: {
          final_artifact: {
            text: "Final answer",
          },
          runtime: {
            runtime_mode: "daytona_pilot",
          },
          run_summary: {
            status: "completed",
          },
        },
        timestamp: new Date().toISOString(),
        version: 3,
        event_id: "evt-2",
      },
    };

    const result = applyWsFrameToMessages([], frame);

    expect(result.terminal).toBe(true);
    expect(result.errored).toBe(false);
    expect(result.messages).toHaveLength(1);
    expect(result.messages[0]).toMatchObject({
      type: "assistant",
      content: "Final answer",
      streaming: false,
    });
  });
});
