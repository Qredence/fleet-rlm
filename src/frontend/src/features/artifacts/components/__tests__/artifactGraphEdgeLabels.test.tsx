import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ArtifactGraph } from "@/features/artifacts/components/ArtifactGraph";
import type { ExecutionStep } from "@/stores/artifactStore";

vi.mock("reactflow", () => {
  const ReactFlow = (props: Record<string, unknown>) =>
    React.createElement(
      "div",
      {
        "data-testid": "reactflow",
        "data-edges": JSON.stringify(props.edges ?? []),
      },
      props.children as React.ReactNode,
    );

  const passthrough = (tag: string) => (props: Record<string, unknown>) =>
    React.createElement(tag, props, props.children as React.ReactNode);

  return {
    __esModule: true,
    default: ReactFlow,
    Background: passthrough("div"),
    Controls: passthrough("div"),
    MiniMap: passthrough("div"),
    Handle: passthrough("div"),
    Position: { Top: "top", Bottom: "bottom" },
    PanOnScrollMode: { Vertical: "vertical" },
  };
});

describe("ArtifactGraph edge elapsed labels", () => {
  it("attaches formatted elapsed labels to sequential edges", () => {
    const steps: ExecutionStep[] = [
      {
        id: "a",
        type: "llm",
        label: "Planner",
        output: { text: "thinking" },
        timestamp: 1000,
      },
      {
        id: "b",
        type: "tool",
        label: "Tool: list_files",
        input: { tool_name: "list_files" },
        output: { result: "ok" },
        timestamp: 1350,
        parent_id: "a",
      },
      {
        id: "c",
        type: "output",
        label: "Final output",
        output: { text: "done" },
        timestamp: 3400,
        parent_id: "b",
      },
    ];

    const html = renderToStaticMarkup(
      <ArtifactGraph steps={steps} onSelectStep={() => {}} />,
    );

    expect(html).toContain('data-testid="reactflow"');
    expect(html).toContain("&quot;label&quot;:&quot;350ms&quot;");
    expect(html).toContain("&quot;label&quot;:&quot;2.0s&quot;");
  });
});
