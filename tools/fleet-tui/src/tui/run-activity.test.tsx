import { renderToString } from "ink";
import { describe, expect, it } from "vitest";

import { RunActivity, runActivityLabel } from "./run-activity.js";
import type { Phase, Run } from "./store.js";

function run(phase: Phase, overrides: Partial<Run> = {}): Run {
  return {
    id: "run-12345678",
    phase,
    statusPhase: null,
    statusDetail: null,
    model: "openai/gpt-5",
    startedAt: Date.now() - 84_000,
    endedAt: null,
    finishReason: null,
    error: null,
    toolCount: 1,
    completedSteps: 2,
    ...overrides,
  };
}

describe("RunActivity", () => {
  it.each(["idle", "completed", "error"] as const)(
    "stays hidden when the run is %s",
    (phase) => {
      expect(
        renderToString(<RunActivity run={run(phase)} interactive={false} />),
      ).toBe("");
    },
  );

  it.each([
    ["submitting", "SUBMITTING"],
    ["running", "RUNNING"],
    ["cancelling", "CANCELLING"],
  ] as const)("uses the local %s phase as its fallback", (phase, expected) => {
    expect(runActivityLabel(run(phase))).toBe(expected);
  });

  it("prefers and normalizes the backend phase while preserving its detail", () => {
    const output = renderToString(
      <RunActivity
        run={run("running", {
          statusPhase: "sandbox_execution",
          statusDetail: "comparing distributions",
        })}
        interactive={false}
      />,
      { columns: 100 },
    );

    expect(output).toContain("SANDBOX EXECUTION");
    expect(output).toContain("comparing distributions");
    expect(output).toContain("2 steps · 1 tool");
    expect(output).toContain("Ctrl+C cancel");
    expect(output).toMatch(/0?1:24/);
  });

  it("shows cancellation immediately instead of a stale backend phase", () => {
    const activeRun = run("cancelling", {
      statusPhase: "sandbox_execution",
      statusDetail: "running code",
    });

    expect(runActivityLabel(activeRun)).toBe("CANCELLING");
  });

  it("renders a deterministic static activity glyph outside an interactive TTY", () => {
    const first = renderToString(
      <RunActivity run={run("running")} interactive={false} />,
    );
    const second = renderToString(
      <RunActivity run={run("running")} interactive={false} />,
    );

    expect(first).toBe(second);
    expect(first).toContain("… RUNNING");
  });
});
