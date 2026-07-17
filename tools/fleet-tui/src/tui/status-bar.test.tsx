import { renderToString } from "ink";
import { describe, expect, it } from "vitest";

import { StatusBar } from "./status-bar.js";
import type { Run } from "./store.js";

describe("StatusBar", () => {
  it("shows quiet run metadata without identifiers or transient activity", () => {
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
        run={run}
        promptTokens={1200}
        completionTokens={300}
        width={120}
      />,
      { columns: 120 },
    );

    const text = output.replaceAll(/\u001b\[[0-9;]*m/g, "");
    expect(text).toContain("model —");
    expect(text).toContain("tokens 1.5k");
    expect(text).toContain("steps 0");
    expect(text).toContain("tools 0");
    expect(text).not.toContain("session");
    expect(text).not.toContain("run-12345678");
    expect(text).not.toContain("run ");
    expect(text).not.toContain("execution");
    expect(text).not.toContain("elapsed");
  });
});
