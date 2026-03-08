import { beforeEach, describe, expect, it } from "vitest";

import { applyWsFrameToArtifacts } from "@/features/rlm-workspace/backendArtifactEventAdapter";
import { useArtifactStore } from "@/stores/artifactStore";
import type { WsServerMessage } from "@/lib/rlm-api";

function makeExecutionStepFrame(
  step: Record<string, unknown>,
  timestamp = "2026-03-01T12:00:00Z",
): WsServerMessage {
  return {
    type: "event",
    data: {
      kind: "status",
      text: "execution step",
      timestamp,
      payload: {
        source_type: "execution_step",
        step,
      },
    },
  };
}

function makeEvent(
  kind: string,
  text: string,
  payload?: Record<string, unknown>,
  timestamp = "2026-03-01T12:00:00Z",
): WsServerMessage {
  return {
    type: "event",
    data: {
      kind: kind as never,
      text,
      payload,
      timestamp,
    },
  };
}

describe("applyWsFrameToArtifacts", () => {
  beforeEach(() => {
    useArtifactStore.getState().clear();
  });

  it("preserves actor and lane metadata from execution_step payloads", () => {
    applyWsFrameToArtifacts(
      makeExecutionStepFrame({
        id: "step-1",
        type: "tool",
        label: "Tool: read_file_slice",
        parent_id: "root",
        depth: 2,
        actor_kind: "delegate",
        actor_id: "delegate-42",
        lane_key: "delegate:delegate-42",
        input: { tool_name: "read_file_slice" },
        output: { status: "ok" },
        timestamp: 1730000000,
      }),
    );

    const state = useArtifactStore.getState();
    const step = state.steps.find((candidate) => candidate.id === "step-1");

    expect(step).toBeDefined();
    expect(step?.depth).toBe(2);
    expect(step?.actor_kind).toBe("delegate");
    expect(step?.actor_id).toBe("delegate-42");
    expect(step?.lane_key).toBe("delegate:delegate-42");
  });

  it("normalizes actor kind aliases", () => {
    applyWsFrameToArtifacts(
      makeExecutionStepFrame({
        id: "step-2",
        type: "llm",
        label: "Reasoning",
        actor_kind: "root",
        depth: "0",
        timestamp: 1730000001,
      }),
    );

    const step = useArtifactStore
      .getState()
      .steps.find((candidate) => candidate.id === "step-2");

    expect(step?.actor_kind).toBe("root_rlm");
    expect(step?.depth).toBe(0);
  });

  it("emits discrete llm/tool steps for reasoning/status/tool_call/tool_result", () => {
    applyWsFrameToArtifacts(
      makeEvent(
        "reasoning_step",
        "Analyze prompt",
        undefined,
        "2026-03-01T12:00:01Z",
      ),
    );
    applyWsFrameToArtifacts(
      makeEvent(
        "status",
        "Planning next step",
        undefined,
        "2026-03-01T12:00:01Z",
      ),
    );
    applyWsFrameToArtifacts(
      makeEvent(
        "tool_call",
        "Running grep",
        { tool_name: "grep", tool_input: { pattern: "foo" } },
        "2026-03-01T12:00:01Z",
      ),
    );
    applyWsFrameToArtifacts(
      makeEvent(
        "tool_result",
        "Done",
        { tool_name: "grep", tool_output: "match line" },
        "2026-03-01T12:00:01Z",
      ),
    );

    const state = useArtifactStore.getState();
    expect(state.steps).toHaveLength(4);
    expect(state.steps.map((step) => step.type)).toEqual([
      "llm",
      "llm",
      "tool",
      "tool",
    ]);
    expect(state.steps.map((step) => step.sequence)).toEqual([1, 2, 3, 4]);
    expect(state.steps.map((step) => step.label)).toEqual([
      "Reasoning",
      "Status",
      "Tool: grep",
      "Tool: grep",
    ]);

    expect(state.steps[2]?.input).toEqual({ pattern: "foo" });
    expect(state.steps[3]?.output).toBe("match line");
  });

  it("uses trajectory_step as fallback only when live trace is absent", () => {
    applyWsFrameToArtifacts(
      makeEvent("trajectory_step", "trace", {
        step_index: 0,
        step_data: {
          thought: "Fallback thought",
          tool_name: "read_file",
          input: { path: "src/index.ts" },
          output: "contents",
        },
      }),
    );

    const fallbackState = useArtifactStore.getState();
    expect(fallbackState.steps).toHaveLength(1);
    expect(fallbackState.steps[0]?.type).toBe("tool");
    expect(fallbackState.steps[0]?.label).toBe("Fallback thought");

    useArtifactStore.getState().clear();

    applyWsFrameToArtifacts(makeEvent("reasoning_step", "Live thought"));
    applyWsFrameToArtifacts(
      makeEvent("trajectory_step", "trace", {
        step_index: 0,
        step_data: {
          thought: "Should be suppressed",
          tool_name: "read_file",
        },
      }),
    );

    const liveState = useArtifactStore.getState();
    expect(liveState.steps).toHaveLength(1);
    expect(liveState.steps[0]?.label).toBe("Reasoning");
  });

  it("orders artifact steps by sequence even when timestamps are out of order", () => {
    applyWsFrameToArtifacts(
      makeEvent(
        "reasoning_step",
        "First arrival",
        undefined,
        "2026-03-01T12:00:10Z",
      ),
    );
    applyWsFrameToArtifacts(
      makeEvent("status", "Second arrival", undefined, "2026-03-01T12:00:00Z"),
    );

    const steps = useArtifactStore.getState().steps;
    expect(steps).toHaveLength(2);
    expect(steps[0]?.label).toBe("Reasoning");
    expect(steps[1]?.label).toBe("Status");
    expect(steps[0]?.sequence).toBe(1);
    expect(steps[1]?.sequence).toBe(2);
    expect((steps[0]?.timestamp ?? 0) > (steps[1]?.timestamp ?? 0)).toBe(true);
  });
});
