import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GraphStepNode } from "@/features/artifacts/components/GraphStepNode";

vi.mock("reactflow", () => ({
  Handle: (props: Record<string, unknown>) =>
    React.createElement("div", { "data-mock": "handle", ...props }),
  Position: { Top: "top", Bottom: "bottom" },
}));

function renderNode(data: Parameters<typeof GraphStepNode>[0]["data"]): string {
  return renderToStaticMarkup(
    <GraphStepNode
      id="node-1"
      type="step"
      selected={false}
      zIndex={0}
      xPos={0}
      yPos={0}
      dragging={false}
      isConnectable={false}
      data={data}
    />,
  );
}

describe("GraphStepNode detail surfaces", () => {
  it("renders REPL code preview affordance and expanded code block", () => {
    const html = renderNode({
      label: "Python REPL",
      type: "repl",
      summary: "Executed code",
      count: 1,
      representativeStepId: "repl-1",
      status: "complete",
      expanded: true,
      input: { code: "def hello():\n    return 'hi'\nprint(hello())" },
      output: { result: "hi" },
    });

    expect(html).toContain("REPL code preview");
    expect(html).toContain("def hello()");
  });

  it("renders error details panel for failed nodes", () => {
    const html = renderNode({
      label: "Execution error",
      type: "output",
      summary: "Failed during execution",
      count: 1,
      representativeStepId: "out-1",
      status: "error",
      expanded: true,
      output: {
        error: {
          message: "ValueError: boom",
          code: "ValueError",
          traceback: "Traceback...\nline 1",
        },
      },
    });

    expect(html).toContain("Error details");
    expect(html).toContain("ValueError: boom");
    expect(html).toContain("Traceback");
  });

  it("renders trajectory Thought/Action/Observation chain", () => {
    const longInput =
      "path=src pattern=**/* include_hidden=true output_mode=content stable_sort=true";
    const longObservation =
      "Found files: src/app/main.py, src/app/router.py, src/core/runtime.py, src/core/state.py";

    const html = renderNode({
      label: "Trajectory step",
      type: "tool",
      summary: "Trajectory summary",
      count: 1,
      representativeStepId: "traj-1",
      status: "complete",
      expanded: true,
      actorKind: "sub_agent",
      depth: 2,
      output: {
        trajectory_step: {
          thought: "Need to inspect files",
          tool_name: "list_files",
          input: longInput,
          output: longObservation,
        },
      },
    });

    expect(html).toContain("Thought → Action → Observation");
    expect(html).toContain("Thought");
    expect(html).toContain("Need to inspect files");
    expect(html).toContain("Action");
    expect(html).toContain("list_files");
    expect(html).toContain(longInput);
    expect(html).toContain("Observation");
    expect(html).toContain(longObservation);
    expect(html).toContain("Sub-agent");
    expect(html).toContain("Depth 2");
    expect(html).not.toContain("…");
  });
});
