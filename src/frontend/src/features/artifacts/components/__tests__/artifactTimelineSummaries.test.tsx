import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ArtifactTimeline } from "@/features/artifacts/components/ArtifactTimeline";
import type { ExecutionStep } from "@/stores/artifactStore";

describe("ArtifactTimeline contextual summaries", () => {
  it("renders type-aware summaries for tool/repl/trajectory/output events", () => {
    const steps: ExecutionStep[] = [
      {
        id: "tool-1",
        type: "tool",
        label: "Tool: list_files",
        input: { tool_name: "list_files", tool_input: { path: "." } },
        output: { tool_output: "README.md\nsrc/\ntests/" },
        timestamp: 1000,
      },
      {
        id: "repl-1",
        type: "repl",
        label: "REPL",
        input: { code: "print('hello')\nprint('world')" },
        output: { result: "hello\nworld" },
        timestamp: 2000,
      },
      {
        id: "traj-1",
        type: "repl",
        label: "Trajectory step",
        output: {
          trajectory_step: {
            thought: "Need to inspect files",
            tool_name: "list_files",
            output: "Found 3 files",
          },
        },
        timestamp: 3000,
      },
      {
        id: "out-1",
        type: "output",
        label: "Final output",
        output: {
          text: "",
          payload: { answer: "done", count: 3 },
        },
        timestamp: 4000,
      },
    ];

    const html = renderToStaticMarkup(
      <ArtifactTimeline steps={steps} onSelectStep={() => {}} />,
    );

    expect(html).toContain("list_files");
    expect(html).toContain("REPL code:");
    expect(html).toContain("Thought:");
    expect(html).toContain("Structured output");
  });
});
