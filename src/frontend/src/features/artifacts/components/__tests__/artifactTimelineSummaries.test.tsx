import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ArtifactTimeline } from "@/features/artifacts/components/ArtifactTimeline";
import type { ExecutionStep } from "@/stores/artifactStore";

describe("ArtifactTimeline contextual summaries", () => {
  it("renders type-aware summaries without truncating payload content", () => {
    const fullThought =
      "Need to inspect files and preserve full context for trace readability across the entire chain.";
    const fullObservation =
      "Found files: README.md, src/main.py, src/router.py, src/runtime.py, tests/test_runtime.py";
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
            thought: fullThought,
            tool_name: "list_files",
            output: fullObservation,
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
    expect(html).toContain(fullThought);
    expect(html).toContain(fullObservation);
    expect(html).toContain("Structured output");
    expect(html).not.toContain("…");
  });
});
