import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useBackendChatRuntime } from "@/features/rlm-workspace/useBackendChatRuntime";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocked = vi.hoisted(() => ({
  toastError: vi.fn(),
  clearArtifactSteps: vi.fn(),
  applyArtifactsFrame: vi.fn(),
  setCreationPhase: vi.fn(),
  daytonaStoreState: {
    reset: vi.fn(),
    beginRun: vi.fn(),
    applyFrame: vi.fn(),
    failRun: vi.fn(),
  },
}));

const mockChatStoreState = {
  messages: [] as Array<{ id: string; type: string; content: string; phase?: number }>,
  turnArtifactsByMessageId: {},
  isStreaming: false,
  sessionId: "session-123",
  runtimeMode: "daytona_pilot" as const,
  daytonaRepoUrl: "",
  daytonaRepoRef: "main",
  daytonaContextPaths: "",
  streamMessage: vi.fn(),
  stopStreaming: vi.fn(),
  resetSession: vi.fn(),
  setMessages: vi.fn(),
  setTurnArtifactsByMessageId: vi.fn(),
  snapshotTurnArtifacts: vi.fn(),
  clearTurnArtifacts: vi.fn(),
  addMessage: vi.fn((message) => {
    mockChatStoreState.messages = [...mockChatStoreState.messages, message];
  }),
};

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: mocked.toastError }),
}));

vi.mock("@/stores/navigationStore", () => ({
  useNavigationStore: () => ({
    setCreationPhase: mocked.setCreationPhase,
    sessionId: "nav-session-1",
  }),
}));

vi.mock("@/stores/artifactStore", () => ({
  useArtifactStore: (selector: (state: { clear: typeof mocked.clearArtifactSteps; steps: [] }) => unknown) =>
    selector({ clear: mocked.clearArtifactSteps, steps: [] }),
}));

vi.mock("@/stores/chatStore", () => ({
  useChatStore: Object.assign(
    (selector?: (state: typeof mockChatStoreState) => unknown) =>
      selector ? selector(mockChatStoreState) : mockChatStoreState,
    {
      getState: () => mockChatStoreState,
    },
  ),
}));

vi.mock("@/features/rlm-workspace/daytona-workbench/daytonaWorkbenchStore", () => ({
  useDaytonaWorkbenchStore: Object.assign(
    (selector: (state: typeof mocked.daytonaStoreState) => unknown) =>
      selector(mocked.daytonaStoreState),
    {
      getState: () => mocked.daytonaStoreState,
    },
  ),
}));

vi.mock("@/features/rlm-workspace/backendChatEventAdapter", () => ({
  applyWsFrameToMessages: vi.fn((messages) => ({ messages })),
}));

vi.mock("@/features/rlm-workspace/backendArtifactEventAdapter", () => ({
  applyWsFrameToArtifacts: mocked.applyArtifactsFrame,
}));

vi.mock("@/lib/rlm-api", () => ({
  sendCommandOverWs: vi.fn(),
  subscribeToExecutionStream: vi.fn(() => () => {}),
  rlmApiConfig: {
    workspaceId: "workspace-1",
    userId: "user-1",
  },
}));

function resetState() {
  mockChatStoreState.messages = [];
  mockChatStoreState.turnArtifactsByMessageId = {};
  mockChatStoreState.isStreaming = false;
  mockChatStoreState.sessionId = "session-123";
  mockChatStoreState.runtimeMode = "daytona_pilot";
  mockChatStoreState.daytonaRepoUrl = "";
  mockChatStoreState.daytonaRepoRef = "main";
  mockChatStoreState.daytonaContextPaths = "";
  mockChatStoreState.streamMessage.mockReset();
  mockChatStoreState.stopStreaming.mockReset();
  mockChatStoreState.resetSession.mockReset();
  mockChatStoreState.setMessages.mockReset();
  mockChatStoreState.setTurnArtifactsByMessageId.mockReset();
  mockChatStoreState.snapshotTurnArtifacts.mockReset();
  mockChatStoreState.clearTurnArtifacts.mockReset();
  mockChatStoreState.addMessage.mockClear();
  mocked.daytonaStoreState.reset.mockReset();
  mocked.daytonaStoreState.beginRun.mockReset();
  mocked.daytonaStoreState.applyFrame.mockReset();
  mocked.daytonaStoreState.failRun.mockReset();
  mocked.clearArtifactSteps.mockReset();
  mocked.applyArtifactsFrame.mockReset();
  mocked.setCreationPhase.mockReset();
  mocked.toastError.mockReset();
}

describe("useBackendChatRuntime Daytona transport failures", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  let runtime: ReturnType<typeof useBackendChatRuntime> | null = null;

  function Harness() {
    runtime = useBackendChatRuntime();
    return null;
  }

  beforeEach(() => {
    resetState();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    runtime = null;
  });

  it("surfaces a Daytona stream failure in the workbench instead of appending a generic chat error", async () => {
    mockChatStoreState.streamMessage.mockRejectedValue(
      new Error("No response arrived from the server within 15 seconds. Try again or check the backend logs."),
    );

    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <Harness />
        </QueryClientProvider>,
      );
    });

    expect(runtime).not.toBeNull();

    act(() => {
      runtime?.setInputValue("Explain Daytona workspace mode.");
    });

    await act(async () => {
      await runtime?.handleSubmit();
    });

    expect(mocked.daytonaStoreState.beginRun).toHaveBeenCalledOnce();
    expect(mocked.daytonaStoreState.failRun).toHaveBeenCalledWith(
      "No response arrived from the server within 15 seconds. Try again or check the backend logs.",
    );
    expect(mockChatStoreState.setMessages).not.toHaveBeenCalled();
    expect(mocked.toastError).toHaveBeenCalledWith("Backend stream failed", {
      description:
        "No response arrived from the server within 15 seconds. Try again or check the backend logs.",
    });
  });
});
