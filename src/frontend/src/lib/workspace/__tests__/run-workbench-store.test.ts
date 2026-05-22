import { beforeEach, describe, expect, it } from "vite-plus/test";

import { useRunWorkbenchStore } from "@/lib/workspace/run-workbench-store";
import type { WsServerMessage } from "@/lib/rlm-api";

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
  });
}

describe("useRunWorkbenchStore", () => {
  beforeEach(() => {
    resetWorkbenchStore();
  });

  it("clears a stale error banner when a new Daytona run begins", () => {
    useRunWorkbenchStore
      .getState()
      .failRun("No response arrived from the server within 60 seconds.");

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
      daytonaMode: "daytona_pilot",
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

  it("ignores removed terminal chat compatibility frames", () => {
    useRunWorkbenchStore.getState().beginRun({
      task: "Inspect the repo",
    });

    useRunWorkbenchStore.getState().applyFrame({
      type: "event",
      data: {
        kind: "done",
        text: "Done",
        event_id: "evt-compat-final",
        payload: {
          runtime_mode: "daytona_pilot",
          mlflow_trace_id: "trace-123",
          mlflow_client_request_id: "chat-123",
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
    } as unknown as WsServerMessage);

    const state = useRunWorkbenchStore.getState();
    expect(state.finalArtifact).toBeNull();
    expect(state.summary).toBeUndefined();
    expect(state.iterations).toEqual([]);
  });
});
