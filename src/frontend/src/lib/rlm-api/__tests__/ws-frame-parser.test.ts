import { describe, expect, it } from "vite-plus/test";
import { parseWsServerFrame } from "@/lib/rlm-api/ws-frame-parser";

describe("parseWsServerFrame", () => {
  it("rejects removed retired event envelopes", () => {
    const frame = parseWsServerFrame({
      type: "event",
      data: {
        kind: "status",
        text: "ok",
        version: 2,
        event_id: "evt-1",
      },
    });

    expect(frame).toBeNull();
  });

  it("maps command_result success to canonical command_result event", () => {
    const frame = parseWsServerFrame({
      type: "command_result",
      command: "hitl.respond",
      result: { status: "ok", value: true },
      version: 2,
      event_id: "evt-command-ack",
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("command_result");
    expect(frame.data.payload?.command).toBe("hitl.respond");
    expect(frame.data.version).toBe(2);
    expect(frame.data.event_id).toBe("evt-command-ack");
  });

  it("maps command_result error to canonical command_result event", () => {
    const frame = parseWsServerFrame({
      type: "command_result",
      command: "hitl.respond",
      result: { status: "error", error: "Denied" },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("command_result");
    expect(frame.data.text).toContain("Denied");
  });

  it("accepts versioned canonical execution event envelopes", () => {
    const frame = parseWsServerFrame({
      type: "event",
      data: {
        kind: "execution_started",
        text: "Execution started",
        version: 2,
        event_id: "evt-1",
      },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("execution_started");
    expect(frame.data.version).toBe(2);
    expect(frame.data.event_id).toBe("evt-1");
  });

  it("maps execution_completed summaries into run_summary payloads", () => {
    const frame = parseWsServerFrame({
      type: "execution_completed",
      output: "Done",
      timestamp: 1710849600,
      summary: {
        run_id: "run-123",
        runtime_mode: "daytona_pilot",
        final_artifact: {
          value: {
            summary: "Execution summary",
          },
        },
        warnings: ["One warning"],
      },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("execution_completed");
    expect(frame.data.payload?.source_type).toBe("execution_completed");
    expect(frame.data.payload?.run_summary).toMatchObject({
      run_id: "run-123",
      runtime_mode: "daytona_pilot",
      warnings: ["One warning"],
    });
    expect(frame.data.text).toBe("Done");
    expect(frame.data.timestamp).toBe(1710849600);
  });

  it("preserves numeric timestamps on execution_step frames", () => {
    const frame = parseWsServerFrame({
      type: "execution_step",
      timestamp: 1710849601,
      step: {
        id: "step-1",
        type: "tool",
        label: "Tool result",
        output: "ok",
        timestamp: 1710849602,
      },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("execution_step");
    expect(frame.data.timestamp).toBe(1710849602);
  });

  it("preserves output steps as canonical execution_step frames", () => {
    const frame = parseWsServerFrame({
      type: "execution_step",
      timestamp: 1710849601,
      step: {
        id: "step-1",
        type: "output",
        label: "assistant_output",
        output: { text: "This is the actual final response text!" },
        timestamp: 1710849602,
      },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("execution_step");
    expect(frame.data.payload?.step).toMatchObject({ type: "output" });
  });

  it("rejects legacy bare-kind envelopes without a type field", () => {
    const frame = parseWsServerFrame({
      kind: "execution_step",
      text: "Planning response.",
      payload: { source_type: "reasoning" },
      timestamp: "2026-06-08T09:00:00.000Z",
      version: 3,
      event_id: "run:4",
      sequence: 4,
    });

    expect(frame).toBeNull();
  });

  it("maps execution_step reasoning phase to reasoning source_type", () => {
    const frame = parseWsServerFrame({
      type: "execution_step",
      step: {
        type: "llm",
        label: "Short reasoning summary",
        input: { phase: "reasoning" },
        output: { text: "Short reasoning summary" },
      },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.payload?.source_type).toBe("reasoning");
    expect(frame.data.text).toBe("Short reasoning summary");
  });

  it("hoists final_reasoning from execution_completed step payload", () => {
    const frame = parseWsServerFrame({
      type: "execution_completed",
      step: {
        output: {
          text: "The sum is 4.",
          payload: { final_reasoning: "I added 2 and 2." },
        },
      },
      summary: { status: "completed" },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.payload?.final_reasoning).toBe("I added 2 and 2.");
    expect(frame.data.text).toBe("The sum is 4.");
  });

  it("preserves raw RLM repl execution steps for chat rendering", () => {
    const frame = parseWsServerFrame({
      type: "execution_step",
      timestamp: 1710849601,
      step: {
        id: "step-rlm-repl",
        type: "repl",
        label: "repl_result",
        input: { code: "print(document_text[:20])" },
        output: { stdout: "DSPy documentation" },
        timestamp: 1710849602,
      },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("execution_step");
    expect(frame.data.text).toBe("repl_result");
    expect(frame.data.payload?.step).toMatchObject({
      type: "repl",
      input: { code: "print(document_text[:20])" },
      output: { stdout: "DSPy documentation" },
    });
  });
});
