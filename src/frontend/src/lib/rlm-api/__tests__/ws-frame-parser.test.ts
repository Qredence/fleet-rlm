import { describe, expect, it } from "vite-plus/test";
import { parseWsServerFrame } from "@/lib/rlm-api/ws-frame-parser";

describe("parseWsServerFrame", () => {
  it("parses versioned event envelopes", () => {
    const frame = parseWsServerFrame({
      type: "event",
      data: {
        kind: "status",
        text: "ok",
        version: 2,
        event_id: "evt-1",
      },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("status");
    expect(frame.data.version).toBe(2);
    expect(frame.data.event_id).toBe("evt-1");
  });

  it("maps command_result success to command_ack event", () => {
    const frame = parseWsServerFrame({
      type: "command_result",
      command: "hitl.respond",
      result: { status: "ok", value: true },
      version: 2,
      event_id: "evt-command-ack",
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("command_ack");
    expect(frame.data.payload?.command).toBe("hitl.respond");
    expect(frame.data.version).toBe(2);
    expect(frame.data.event_id).toBe("evt-command-ack");
  });

  it("maps command_result error to command_reject event", () => {
    const frame = parseWsServerFrame({
      type: "command_result",
      command: "hitl.respond",
      result: { status: "error", error: "Denied" },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("command_reject");
    expect(frame.data.text).toContain("Denied");
  });

  it("accepts warning stream events", () => {
    const frame = parseWsServerFrame({
      type: "event",
      data: {
        kind: "warning",
        text: "Dataset was partially truncated",
      },
    });

    expect(frame).toBeTruthy();
    if (!frame || frame.type !== "event") return;
    expect(frame.data.kind).toBe("warning");
    expect(frame.data.text).toContain("truncated");
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
    expect(frame.data.kind).toBe("final");
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
    expect(frame.data.kind).toBe("tool_result");
    expect(frame.data.timestamp).toBe(1710849602);
  });
});
