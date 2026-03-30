import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";

import { RunWorkbench } from "@/app/workspace/workbench/run-workbench";
import type { DetailTab, RunWorkbenchState } from "@/lib/workspace/workspace-types";

type MockedRunWorkbenchStore = RunWorkbenchState & {
  selectIteration: (iterationId: string | null) => void;
  selectCallback: (callbackId: string | null) => void;
  selectTab: (tab: DetailTab) => void;
};

const mockedWorkbenchStore: MockedRunWorkbenchStore = {
  status: "running",
  runId: "run-123",
  repoUrl: "https://github.com/qredence/fleet-rlm",
  repoRef: "main",
  daytonaMode: "host_loop_rlm",
  task: "Inspect tracing architecture",
  contextSources: [
    {
      sourceId: "ctx-1",
      kind: "directory",
      hostPath: "/Users/zocho/Documents/specs",
      fileCount: 4,
    },
  ],
  iterations: [
    {
      id: "iteration-1",
      iteration: 1,
      status: "running",
      phase: "planning",
      summary: "Root iteration is sweeping the repo and corpus.",
      reasoningSummary: "Planner selected a grounded architecture pass.",
      durationMs: 12,
      callbackCount: 1,
    },
  ],
  callbacks: [
    {
      id: "callback-1",
      callbackName: "llm_query_batched",
      iteration: 1,
      status: "completed",
      task: "Summarize tracing adapters",
      resultPreview: "Tracing spans flow through analytics and UI adapters.",
      source: {
        path: "src/fleet_rlm/analytics/scorers.py",
        startLine: 1,
        endLine: 30,
      },
    },
  ],
  promptHandles: [
    {
      handleId: "prompt-1",
      label: "Task prompt",
      kind: "manual",
      preview: "Inspect the tracing pipeline and summarize the architecture.",
    },
  ],
  sources: [
    {
      sourceId: "ctx-1",
      kind: "file",
      title: "spec.pdf",
      displayUrl: "/Users/zocho/Documents/specs/spec.pdf",
      description: "Staged diligence specification.",
    },
  ],
  attachments: [
    {
      attachmentId: "ctx-1",
      name: "spec.pdf",
      mimeType: "pdf",
      description: "Host path: /Users/zocho/Documents/specs/spec.pdf",
    },
  ],
  activity: [
    {
      id: "evt-1",
      kind: "status",
      text: "Root iteration running",
      iteration: 1,
      status: "running",
      phase: "planning",
    },
  ],
  selectedIterationId: "iteration-1",
  selectedCallbackId: "callback-1",
  selectIteration: vi.fn(),
  selectCallback: vi.fn(),
  selectedTab: "iterations",
  selectTab: vi.fn(),
  finalArtifact: {
    kind: "markdown",
    finalizationMode: "SUBMIT",
    textPreview: "Tracing data flows through analytics scorers into the UI.",
    value: {
      summary: "Tracing data flows through analytics scorers into the UI.",
    },
  },
  summary: {
    durationMs: 1234,
    terminationReason: "running",
    warnings: ["One callback result was truncated before rendering."],
  },
  errorMessage: null,
  compatBackfillCount: 0,
  lastCompatBackfill: null,
};

vi.mock("@/screens/workspace/use-workspace", () => ({
  useRunWorkbenchStore: () => mockedWorkbenchStore,
}));

describe("RunWorkbench", () => {
  it("renders the analyst-oriented tabs and hides legacy tree framing", () => {
    const html = renderToStaticMarkup(<RunWorkbench />);

    expect(html).toContain("Workspace execution");
    expect(html).toContain("Iterations");
    expect(html).toContain("Evidence");
    expect(html).toContain("Final Output");
    expect(html).not.toContain(">Callbacks<");
    expect(html).not.toContain(">Prompts<");
    expect(html).toContain("Inspect tracing architecture");
    expect(html).toContain("1 iterations");
    expect(html).toContain("1 callbacks");
    expect(html).toContain("1 prompt objects");
    expect(html).not.toContain("Timeline");
    expect(html).not.toContain("Node");
    expect(html).not.toContain("Run tree");
  });

  it("renders extracted markdown text instead of raw artifact wrapper JSON", () => {
    mockedWorkbenchStore.selectedTab = "final";
    mockedWorkbenchStore.finalArtifact = {
      kind: "markdown",
      textPreview: "Hello there, it is great to meet you!",
      value: {
        final_markdown: "Hello there, it is great to meet you!",
      },
    };

    const html = renderToStaticMarkup(<RunWorkbench />);

    expect(html).toContain("Hello there, it is great to meet you!");
    expect(html).not.toContain("final_markdown");

    mockedWorkbenchStore.selectedTab = "iterations";
  });
});
