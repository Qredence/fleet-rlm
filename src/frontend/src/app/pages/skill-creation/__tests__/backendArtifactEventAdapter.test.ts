import { beforeEach, describe, expect, it } from "vitest";

import { applyWsFrameToArtifacts } from "@/app/pages/skill-creation/backendArtifactEventAdapter";
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
});
