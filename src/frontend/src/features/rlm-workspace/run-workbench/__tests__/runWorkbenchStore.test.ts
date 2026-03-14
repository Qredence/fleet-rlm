import { beforeEach, describe, expect, it } from "vitest";

import { useRunWorkbenchStore } from "@/features/rlm-workspace/run-workbench/runWorkbenchStore";

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
});
