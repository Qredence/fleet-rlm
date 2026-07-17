import { renderToString } from "ink";
import { describe, expect, it } from "vitest";

import { StatusBar } from "./status-bar.js";
import type { Run } from "./store.js";

describe("StatusBar", () => {
  it("shows transient execution activity while the Run is active", () => {
    const run: Run = {
      id: "run-12345678",
      phase: "running",
      statusPhase: "execution",
      statusDetail: "running",
      model: null,
      startedAt: Date.now(),
      endedAt: null,
      finishReason: null,
      error: null,
      toolCount: 0,
      completedSteps: 0,
    };

    const output = renderToString(
      <StatusBar
        session={null}
        run={run}
        promptTokens={0}
        completionTokens={0}
        width={120}
      />,
      { columns: 120 },
    );

    expect(output).toContain("execution · running");
  });
});
