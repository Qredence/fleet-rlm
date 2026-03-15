import { describe, expect, it } from "vite-plus/test";
import { parseWsServerFrame } from "@/lib/rlm-api/wsFrameParser";

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
});
