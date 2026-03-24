import { beforeEach, describe, expect, it, vi } from "vite-plus/test";

vi.mock("@/lib/telemetry/client", () => ({
  telemetryClient: {
    capture: vi.fn(),
  },
}));

import { useRunWorkbenchStore } from "@/screens/workspace/use-workspace";
import { telemetryClient } from "@/lib/telemetry/client";

function resetWorkbenchStore() {
  useRunWorkbenchStore.setState({
    status: "idle",
    runId: undefined,
    repoUrl: undefined,
    repoRef: null,
    daytonaMode: undefined,
    task: undefined,
    contextSources: [],
    iterations: [],
    callbacks: [],
    promptHandles: [],
    sources: [],
    attachments: [],
    activity: [],
    selectedIterationId: null,
    selectedCallbackId: null,
    selectedTab: "iterations",
    finalArtifact: null,
    summary: undefined,
    errorMessage: null,
    lastFrame: null,
    compatBackfillCount: 0,
    lastCompatBackfill: null,
  });
}

describe("useRunWorkbenchStore", () => {
  beforeEach(() => {
    resetWorkbenchStore();
    vi.clearAllMocks();
  });

  it("clears a stale error banner when a new Daytona run begins", () => {
    useRunWorkbenchStore
      .getState()
      .failRun("No response arrived from the server within 15 seconds.");

    useRunWorkbenchStore.getState().beginRun({
      task: "Say hello in one sentence.",
    });

    const state = useRunWorkbenchStore.getState();
    expect(state.status).toBe("bootstrapping");
    expect(state.errorMessage).toBeNull();
    expect(state.activity).toEqual([]);
    expect(state.selectedIterationId).toBeNull();
    expect(state.finalArtifact).toBeNull();
  });

  it("reset clears lingering analyst workbench state fields", () => {
    useRunWorkbenchStore.getState().failRun("Old Daytona failure");
    useRunWorkbenchStore.getState().beginRun({
      task: "Inspect the repo",
      repoUrl: "https://github.com/qredence/fleet-rlm.git",
      repoRef: "main",
      contextPaths: ["/tmp/context.md"],
    });

    useRunWorkbenchStore.setState({
      status: "running",
      runId: "run-123",
      daytonaMode: "host_loop_rlm",
      errorMessage: "Should disappear",
      selectedIterationId: "iteration-1",
      selectedCallbackId: "callback-1",
    });

    useRunWorkbenchStore.getState().reset();

    const state = useRunWorkbenchStore.getState();
    expect(state.status).toBe("idle");
    expect(state.runId).toBeUndefined();
    expect(state.daytonaMode).toBeUndefined();
    expect(state.errorMessage).toBeNull();
    expect(state.selectedIterationId).toBeNull();
    expect(state.selectedCallbackId).toBeNull();
    expect(state.contextSources).toEqual([]);
    expect(state.activity).toEqual([]);
  });

  it("tracks telemetry when terminal chat frames backfill only summary and final artifact", () => {
    useRunWorkbenchStore.getState().beginRun({
      task: "Inspect the repo",
    });

    useRunWorkbenchStore.getState().applyFrame({
      type: "event",
      data: {
        kind: "final",
        text: "Done",
        event_id: "evt-compat-final",
        payload: {
          runtime_mode: "daytona_pilot",
          run_result: {
            run_id: "run-123",
            task: "Inspect the repo",
            iterations: [
              {
                iteration: 1,
                status: "completed",
                summary: "Should stay ignored",
              },
            ],
            final_artifact: {
              kind: "markdown",
              value: { summary: "Compatibility summary" },
            },
            summary: {
              termination_reason: "completed",
              warnings: ["late execution summary"],
            },
          },
        },
      },
    });

    const state = useRunWorkbenchStore.getState();
    expect(state.finalArtifact?.value).toMatchObject({
      summary: "Compatibility summary",
    });
    expect(state.summary?.warnings).toEqual(["late execution summary"]);
    expect(state.iterations).toEqual([]);
    expect(state.compatBackfillCount).toBe(1);
    expect(state.lastCompatBackfill).toMatchObject({
      eventId: "evt-compat-final",
      runtimeMode: "daytona_pilot",
      usedSummary: true,
      usedFinalArtifact: true,
    });
    expect(telemetryClient.capture).toHaveBeenCalledWith(
      "run_workbench_chat_final_backfill_used",
      expect.objectContaining({
        runtime_mode: "daytona_pilot",
        used_summary: true,
        used_final_artifact: true,
      }),
    );
  });
});
