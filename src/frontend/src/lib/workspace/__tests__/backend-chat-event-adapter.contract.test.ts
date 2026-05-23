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
