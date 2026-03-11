import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { DaytonaWorkbench } from "@/features/rlm-workspace/daytona-workbench/DaytonaWorkbench";

vi.mock(
  "@/features/rlm-workspace/daytona-workbench/daytonaWorkbenchStore",
  () => ({
    useDaytonaWorkbenchStore: () => ({
      status: "running",
      runId: "run-123",
      repoUrl: "https://github.com/qredence/fleet-rlm",
      repoRef: "main",
      daytonaMode: "recursive_rlm",
      task: "Inspect tracing architecture",
      contextSources: [
        {
          sourceId: "ctx-1",
          kind: "directory",
          hostPath: "/Users/zocho/Documents/specs",
          fileCount: 4,
        },
      ],
      rootId: "root-node",
      nodes: {
        "root-node": {
          nodeId: "root-node",
          parentId: null,
          depth: 0,
          task: "Inspect tracing architecture",
          status: "running",
          sandboxId: "sandbox-root",
          workspacePath: "/workspace",
          warnings: ["One child sandbox did not terminate cleanly."],
          promptHandles: [
            {
              handleId: "prompt-1",
              label: "Task prompt",
              kind: "manual",
              preview: "Inspect the tracing pipeline and summarize the architecture.",
            },
          ],
          childIds: ["child-1"],
          childLinks: [
            {
              childId: "child-1",
              callbackName: "llm_query_batched",
              status: "completed",
              resultPreview: "Tracing spans flow through analytics and UI adapters.",
              task: {
                task: "Summarize tracing adapters",
                source: {
                  path: "src/fleet_rlm/analytics/scorers.py",
                  startLine: 1,
                  endLine: 30,
                },
              },
            },
          ],
          finalArtifact: {
            kind: "markdown",
            textPreview: "Tracing data flows through analytics scorers into the UI.",
            value: { summary: "Tracing data flows through analytics scorers into the UI." },
          },
        },
      },
      nodeOrder: ["root-node"],
      timeline: [
        {
          id: "evt-1",
          kind: "status",
          text: "Root node running",
          nodeId: "root-node",
          status: "running",
          phase: "planning",
        },
      ],
      selectedNodeId: "root-node",
      selectNode: vi.fn(),
      selectedTab: "timeline",
      selectTab: vi.fn(),
      finalArtifact: {
        kind: "markdown",
        finalizationMode: "SUBMIT",
        textPreview: "Tracing data flows through analytics scorers into the UI.",
        value: { summary: "Tracing data flows through analytics scorers into the UI." },
      },
      summary: {
        durationMs: 1234,
        terminationReason: "running",
        warnings: ["One child sandbox did not terminate cleanly."],
      },
      errorMessage: null,
    }),
  }),
);

describe("DaytonaWorkbench", () => {
  it("renders a compact inspector layout with tree-first details tabs", () => {
    const html = renderToStaticMarkup(<DaytonaWorkbench />);

    expect(html).toContain("Daytona Workbench");
    expect(html).toContain("Run tree");
    expect(html).toContain("Details");
    expect(html).toContain("External sources");
    expect(html).toContain("recursive_rlm");
    expect(html).toContain("Directory");
    expect(html).toContain("Timeline");
    expect(html).toContain("Prompts");
    expect(html).toContain("Final");
    expect(html).toContain("Cancellation warnings");
    expect(html).not.toContain("Live timeline");
  });
});
