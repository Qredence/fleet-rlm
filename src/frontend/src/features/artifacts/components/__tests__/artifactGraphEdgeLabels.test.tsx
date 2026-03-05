import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ArtifactGraph } from "@/features/artifacts/components/ArtifactGraph";
import type { ExecutionStep } from "@/stores/artifactStore";

vi.mock("reactflow", () => {
  const ReactFlow = (props: Record<string, unknown>) =>
    React.createElement(
      "div",
      {
        "data-testid": "reactflow",
        "data-edges": JSON.stringify(props.edges ?? []),
        "data-nodes": JSON.stringify(props.nodes ?? []),
      },
      props.children as React.ReactNode,
    );

  const passthrough = (tag: string) => (props: Record<string, unknown>) =>
    React.createElement(tag, props, props.children as React.ReactNode);

  const MiniMap = ({
    pannable: _pannable,
    zoomable: _zoomable,
    nodeStrokeWidth: _nodeStrokeWidth,
    nodeColor: _nodeColor,
    children,
    ...rest
  }: Record<string, unknown>) =>
    React.createElement("div", rest, children as React.ReactNode);

  return {
    __esModule: true,
    default: ReactFlow,
    Background: passthrough("div"),
    Controls: passthrough("div"),
    MiniMap,
    Handle: passthrough("div"),
    Position: { Top: "top", Bottom: "bottom" },
    PanOnScrollMode: { Vertical: "vertical" },
  };
});

describe("ArtifactGraph edge elapsed labels", () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    expect(consoleErrorSpy).not.toHaveBeenCalled();
    consoleErrorSpy.mockRestore();
  });

  it("attaches formatted elapsed labels to chronological edges", () => {
    const steps: ExecutionStep[] = [
      {
        id: "a",
        type: "llm",
        label: "Planner",
        output: { text: "thinking" },
        timestamp: 1000,
        actor_kind: "root_rlm",
        depth: 0,
      },
      {
        id: "b",
        type: "tool",
        label: "Tool: list_files",
        input: { tool_name: "list_files" },
        output: { result: "ok" },
        timestamp: 1350,
        parent_id: "a",
        actor_kind: "sub_agent",
        depth: 1,
      },
      {
        id: "c",
        type: "output",
        label: "Final output",
        output: { text: "done" },
        timestamp: 3400,
        parent_id: "b",
        actor_kind: "delegate",
        depth: 2,
      },
    ];

    const html = renderToStaticMarkup(
      <ArtifactGraph steps={steps} onSelectStep={() => {}} />,
    );

    expect(html).toContain('data-testid="reactflow"');
    expect(html).toContain("&quot;label&quot;:&quot;350ms&quot;");
    expect(html).toContain("&quot;label&quot;:&quot;2.0s&quot;");
    expect(html).toContain("Root RLM");
    expect(html).toContain("Sub-agent (depth 1)");
    expect(html).toContain("Delegate (depth 2)");
  });

  it("does not collapse contiguous tool steps into grouped nodes", () => {
    const steps: ExecutionStep[] = [
      {
        id: "llm-1",
        type: "llm",
        label: "Planner",
        output: { text: "reasoning" },
        timestamp: 1000,
      },
      {
        id: "tool-1",
        type: "tool",
        label: "Tool: list_files",
        input: { tool_name: "list_files" },
        output: { result: "first" },
        parent_id: "llm-1",
        timestamp: 1100,
      },
      {
        id: "tool-2",
        type: "tool",
        label: "Tool: list_files",
        input: { tool_name: "list_files" },
        output: { result: "second" },
        parent_id: "llm-1",
        timestamp: 1200,
      },
    ];

    const html = renderToStaticMarkup(
      <ArtifactGraph steps={steps} onSelectStep={() => {}} />,
    );
    expect(html).toContain('data-testid="reactflow"');
    expect(html).toContain("&quot;id&quot;:&quot;node-llm-1&quot;");
    expect(html).toContain("&quot;id&quot;:&quot;node-tool-1&quot;");
    expect(html).toContain("&quot;id&quot;:&quot;node-tool-2&quot;");
  });
});
