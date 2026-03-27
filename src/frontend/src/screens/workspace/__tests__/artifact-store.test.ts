import { beforeEach, describe, expect, it } from "vite-plus/test";
import { useArtifactStore } from "@/screens/workspace/use-workspace";

describe("artifactStore sequence ordering", () => {
  beforeEach(() => {
    useArtifactStore.getState().clear();
  });

  it("assigns sequence on add and keeps insertion order even when timestamps are out of order", () => {
    const store = useArtifactStore.getState();

    store.addStep({
      id: "step-a",
      type: "llm",
      label: "A",
      timestamp: 2000,
    });
    store.addStep({
      id: "step-b",
      type: "llm",
      label: "B",
      timestamp: 1000,
    });

    const steps = useArtifactStore.getState().steps;
    expect(steps.map((step) => step.id)).toEqual(["step-a", "step-b"]);
    expect(steps.map((step) => step.sequence)).toEqual([1, 2]);
  });

  it("preserves existing sequence on upsert when incoming step omits sequence", () => {
    const store = useArtifactStore.getState();

    store.addStep({
      id: "step-a",
      type: "llm",
      label: "A",
      timestamp: 1000,
    });
    store.addStep({
      id: "step-b",
      type: "tool",
      label: "B",
      timestamp: 1001,
    });

    store.upsertStep({
      id: "step-a",
      type: "llm",
      label: "A updated",
      timestamp: 500,
    });

    const steps = useArtifactStore.getState().steps;
    expect(steps.map((step) => step.id)).toEqual(["step-a", "step-b"]);
    expect(steps.map((step) => step.sequence)).toEqual([1, 2]);
    expect(steps[0]?.label).toBe("A updated");
  });

  it("sorts by explicit sequence before timestamp/id fallback", () => {
    const store = useArtifactStore.getState();

    store.setSteps([
      {
        id: "step-c",
        type: "output",
        label: "C",
        sequence: 3,
        timestamp: 10,
      },
      {
        id: "step-a",
        type: "llm",
        label: "A",
        sequence: 1,
        timestamp: 30,
      },
      {
        id: "step-b",
        type: "tool",
        label: "B",
        sequence: 2,
        timestamp: 20,
      },
    ]);

    const steps = useArtifactStore.getState().steps;
    expect(steps.map((step) => step.id)).toEqual(["step-a", "step-b", "step-c"]);
  });
});
