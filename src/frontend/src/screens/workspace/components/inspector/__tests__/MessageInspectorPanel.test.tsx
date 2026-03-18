import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { MessageInspectorPanel } from "@/screens/workspace/components/inspector/MessageInspectorPanel";
import type { ChatMessage } from "@/screens/workspace/model/workspace-types";
import type { ExecutionStep } from "@/screens/workspace/model/artifact-types";
import { useChatStore } from "@/screens/workspace/model/chat-store";
import { useWorkspaceUiStore } from "@/screens/workspace/model/workspace-ui-store";

vi.mock("@/screens/workspace/components/inspector/artifact-graph", () => ({
  ArtifactGraph: ({ steps }: { steps: ExecutionStep[] }) => (
    <div data-testid="artifact-graph">{steps.length} steps</div>
  ),
}));

function mountInspector() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(<MessageInspectorPanel />);
  });

  return { container, root };
}

function setInspectorState(options: {
  messages: ChatMessage[];
  selectedAssistantTurnId?: string | null;
  activeInspectorTab?: "trajectory" | "execution" | "evidence" | "graph";
  turnArtifactsByMessageId?: Record<string, ExecutionStep[]>;
}) {
  useChatStore.setState({
    messages: options.messages,
    turnArtifactsByMessageId: options.turnArtifactsByMessageId ?? {},
    isStreaming: false,
    error: null,
  });

  useWorkspaceUiStore.setState({
    selectedAssistantTurnId: options.selectedAssistantTurnId ?? null,
    activeInspectorTab: options.activeInspectorTab ?? "trajectory",
  });
}

function linearSteps(): ExecutionStep[] {
  return [
    {
      id: "step-1",
      type: "llm",
      label: "Plan response",
      timestamp: 1,
      actor_kind: "root_rlm",
      actor_id: "root",
      lane_key: "root",
    },
    {
      id: "step-2",
      parent_id: "step-1",
      type: "tool",
      label: "Read file",
      timestamp: 2,
      actor_kind: "root_rlm",
      actor_id: "root",
      lane_key: "root",
    },
  ];
}

function branchedSteps(): ExecutionStep[] {
  return [
    {
      id: "step-1",
      type: "llm",
      label: "Plan response",
      timestamp: 1,
      actor_kind: "root_rlm",
      actor_id: "root",
      lane_key: "root",
    },
    {
      id: "step-2",
      parent_id: "step-1",
      type: "tool",
      label: "Read file",
      timestamp: 2,
      actor_kind: "root_rlm",
      actor_id: "root",
      lane_key: "root",
    },
    {
      id: "step-3",
      parent_id: "step-1",
      type: "llm",
      label: "Delegate sub-task",
      timestamp: 3,
      actor_kind: "delegate",
      actor_id: "delegate-1",
      lane_key: "delegate-1",
    },
  ];
}

describe("MessageInspectorPanel", () => {
  beforeEach(() => {
    useChatStore.setState({
      messages: [],
      turnArtifactsByMessageId: {},
      isStreaming: false,
      error: null,
    });
    useWorkspaceUiStore.setState({
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectory",
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("shows the empty inspector state until an assistant turn is selected", () => {
    setInspectorState({
      messages: [
        {
          id: "assistant-1",
          type: "assistant",
          content: "Here is the answer.",
          streaming: false,
        },
      ],
      selectedAssistantTurnId: null,
    });

    const { container, root } = mountInspector();

    expect(container.textContent).toContain("Message Inspector");
    expect(container.textContent).toContain(
      "Select an assistant response in the chat to inspect its trajectory, execution details, evidence, and relationships.",
    );

    act(() => {
      root.unmount();
    });
  });

  it("hides the graph tab for linear turns and falls back to trajectory", () => {
    setInspectorState({
      messages: [
        {
          id: "assistant-1",
          type: "assistant",
          content: "Linear turn",
          streaming: false,
        },
      ],
      selectedAssistantTurnId: "assistant-1",
      activeInspectorTab: "graph",
      turnArtifactsByMessageId: {
        "assistant-1": linearSteps(),
      },
    });

    const { container, root } = mountInspector();
    const tabs = Array.from(container.querySelectorAll('[role="tab"]')).map((tab) =>
      tab.textContent?.trim(),
    );

    expect(tabs).toEqual(["Trajectory"]);
    expect(container.textContent).not.toContain("Graph");
    expect(useWorkspaceUiStore.getState().activeInspectorTab).toBe("trajectory");

    act(() => {
      root.unmount();
    });
  });

  it("uses a summary block header and shows full trajectory body text", () => {
    setInspectorState({
      messages: [
        {
          id: "trace-cot",
          type: "trace",
          content: "trajectory",
          traceSource: "live",
          renderParts: [
            {
              kind: "chain_of_thought",
              title: "Trajectory",
              steps: [
                {
                  id: "step-1",
                  index: 0,
                  label: "Inspect workspace structure",
                  status: "complete",
                  details: [
                    "This is a long trajectory body that should remain fully visible inside the inspector without being clipped into a teaser.",
                  ],
                },
              ],
            },
          ],
        },
        {
          id: "assistant-1",
          type: "assistant",
          content: "Readable answer",
          streaming: false,
        },
      ],
      selectedAssistantTurnId: "assistant-1",
    });

    const { container, root } = mountInspector();

    expect(container.textContent).toContain("Completed");
    expect(container.textContent).not.toContain("Selected assistant turn");
    expect(container.textContent).not.toContain("Selected response");
    expect(container.textContent).toContain(
      "This is a long trajectory body that should remain fully visible inside the inspector without being clipped into a teaser.",
    );

    act(() => {
      root.unmount();
    });
  });

  it("renders tabs in trajectory-execution-evidence-graph order for complex turns", () => {
    setInspectorState({
      messages: [
        {
          id: "trace-reasoning",
          type: "trace",
          content: "reasoning",
          traceSource: "live",
          renderParts: [
            {
              kind: "reasoning",
              parts: [{ type: "text", text: "Inspect the turn." }],
              isStreaming: false,
            },
          ],
        },
        {
          id: "trace-tool",
          type: "trace",
          content: "tool call",
          traceSource: "live",
          renderParts: [
            {
              kind: "tool",
              title: "read_file",
              toolType: "read_file",
              state: "output-available",
              input: { path: "README.md" },
              output: "Loaded",
            },
          ],
        },
        {
          id: "assistant-1",
          type: "assistant",
          content: "Complex turn",
          streaming: false,
          renderParts: [
            {
              kind: "sources",
              title: "Sources",
              sources: [
                {
                  sourceId: "src-1",
                  kind: "file",
                  title: "README.md",
                  description: "Usage notes",
                },
              ],
            },
          ],
        },
      ],
      selectedAssistantTurnId: "assistant-1",
      activeInspectorTab: "graph",
      turnArtifactsByMessageId: {
        "assistant-1": branchedSteps(),
      },
    });

    const { container, root } = mountInspector();
    const tabs = Array.from(container.querySelectorAll('[role="tab"]')).map((tab) =>
      tab.textContent?.trim(),
    );
    const tabList = container.querySelector('[role="tablist"]');
    const tabsRoot = tabList?.parentElement?.parentElement as HTMLElement | null;

    expect(tabs).toEqual(["Trajectory", "Execution", "Evidence", "Graph"]);
    expect(tabsRoot?.classList.contains("flex")).toBe(true);
    expect(tabsRoot?.classList.contains("flex-col")).toBe(true);
    expect(container.textContent).toContain("Relationships");
    expect(container.querySelector('[data-testid="artifact-graph"]')?.textContent).toContain(
      "3 steps",
    );

    act(() => {
      root.unmount();
    });
  });
});
