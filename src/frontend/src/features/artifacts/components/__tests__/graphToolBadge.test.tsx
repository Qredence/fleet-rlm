import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GraphStepNode } from "@/features/artifacts/components/GraphStepNode";
import { extractToolBadgeFromStep } from "@/features/artifacts/components/graphToolBadge";
import type { ExecutionStep } from "@/stores/artifactStore";

vi.mock("reactflow", () => ({
  Handle: (props: Record<string, unknown>) =>
    React.createElement("div", { "data-mock": "handle", ...props }),
  Position: { Top: "top", Bottom: "bottom" },
}));

describe("artifact graph tool badge", () => {
  it("extracts tool badge from payload before label fallback", () => {
    const step: ExecutionStep = {
      id: "tool-1",
      type: "tool",
      label: "Tool: fallback_name",
      input: { tool_name: "list_files" },
      output: { result: "ok" },
      timestamp: 1,
    };

    expect(extractToolBadgeFromStep(step)).toEqual({
      toolName: "list_files",
      toolNameSource: "payload",
    });
  });

  it("falls back to parsing Tool label when payload lacks tool_name", () => {
    const step: ExecutionStep = {
      id: "tool-2",
      type: "tool",
      label: "Tool: grep",
      input: { pattern: "foo" },
      output: "done",
      timestamp: 2,
    };

    expect(extractToolBadgeFromStep(step)).toEqual({
      toolName: "grep",
      toolNameSource: "label",
    });
  });

  it("does not emit tool badge for non-tool/non-repl steps", () => {
    const step: ExecutionStep = {
      id: "llm-1",
      type: "llm",
      label: "Planner",
      output: { text: "thinking" },
      timestamp: 3,
    };

    expect(extractToolBadgeFromStep(step)).toEqual({});
  });

  it("renders a compact tool badge on graph step nodes", () => {
    const html = renderToStaticMarkup(
      <GraphStepNode
        id="node-1"
        type="step"
        selected={false}
        zIndex={0}
        xPos={0}
        yPos={0}
        dragging={false}
        isConnectable={false}
        data={{
          label: "Tool: list_files",
          type: "tool",
          summary: "Lists files in repo root",
          count: 1,
          representativeStepId: "tool-1",
          toolName: "list_files",
          toolNameSource: "payload",
          status: "complete",
          elapsedMs: 120,
        }}
      />,
    );

    expect(html).toContain("list_files");
    expect(html).toContain('data-mock="handle"');
  });
});
