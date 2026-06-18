import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  useArtifactStore,
  useChatStore,
  useRunWorkbenchStore,
  useWorkspace,
  useWorkspaceUiStore,
} from "@/features/workspace/use-workspace";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const mocked = vi.hoisted(() => ({
  toastError: vi.fn(),
  streamMessage: vi.fn(),
  sessionsTurns: vi.fn(),
  stopStreaming: vi.fn(),
  resetSession: vi.fn(),
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

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: mocked.toastError }),
}));

vi.mock("@/lib/workspace/backend-chat-event-adapter", () => ({
  applyWsFrameToMessages: vi.fn((messages) => ({ messages })),
}));

vi.mock("@/lib/workspace/backend-artifact-event-adapter", () => ({
  applyWsFrameToArtifacts: mocked.applyArtifactsFrame,
}));

vi.mock("@/lib/rlm-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/rlm-api")>("@/lib/rlm-api");

  return {
    ...actual,
    sendCommandOverWs: vi.fn(),
    sessionsEndpoints: {
      ...actual.sessionsEndpoints,
      turns: mocked.sessionsTurns,
    },
    subscribeToExecutionStream: vi.fn(() => () => {}),
    rlmApiConfig: {
      ...actual.rlmApiConfig,
      workspaceId: "workspace-1",
      userId: "user-1",
    },
  } as unknown as typeof actual;
});

function resetState() {
  useChatStore.getState().clearMessages();
  useChatStore.setState({
    isStreaming: false,
    sessionId: "session-123",
    runtimeMode: "daytona_pilot",
    streamController: null,
    streamMessage: mocked.streamMessage,
    stopStreaming: mocked.stopStreaming,
    resetSession: mocked.resetSession,
  });
  useWorkspaceUiStore.setState({
    sessionRevision: 0,
    setCreationPhase: mocked.setCreationPhase,
  });
  useArtifactStore.setState({
    steps: [],
    activeStepId: undefined,
    clear: mocked.clearArtifactSteps,
  });
  useRunWorkbenchStore.getState().reset();
  useRunWorkbenchStore.setState({
    reset: mocked.daytonaStoreState.reset,
    beginRun: mocked.daytonaStoreState.beginRun,
    applyFrame: mocked.daytonaStoreState.applyFrame,
    failRun: mocked.daytonaStoreState.failRun,
  });

  mocked.streamMessage.mockReset();
  mocked.sessionsTurns.mockReset();
  mocked.stopStreaming.mockReset();
  mocked.resetSession.mockReset();
  mocked.daytonaStoreState.reset.mockReset();
  mocked.daytonaStoreState.beginRun.mockReset();
  mocked.daytonaStoreState.applyFrame.mockReset();
  mocked.daytonaStoreState.failRun.mockReset();
  mocked.clearArtifactSteps.mockReset();
  mocked.applyArtifactsFrame.mockReset();
  mocked.setCreationPhase.mockReset();
  mocked.toastError.mockReset();
}

describe("useWorkspace Daytona transport failures", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  let runtime: ReturnType<typeof useWorkspace> | null = null;

  function Harness() {
    runtime = useWorkspace();
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
    mocked.streamMessage.mockRejectedValue(
      new Error(
        "No response arrived from the server within 60 seconds. Try again or check the backend logs.",
      ),
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
      "No response arrived from the server within 60 seconds. Try again or check the backend logs.",
    );
    expect(useChatStore.getState().messages).toEqual([
      expect.objectContaining({
        type: "user",
        content: "Explain Daytona workspace mode.",
      }),
    ]);
    expect(mocked.toastError).toHaveBeenCalledWith("Backend stream failed", {
      description:
        "No response arrived from the server within 60 seconds. Try again or check the backend logs.",
    });
  });

  it("loads durable saved conversations from backend turns", async () => {
    mocked.sessionsTurns.mockResolvedValue({
      items: [
        {
          id: "turn-1",
          turn_index: 0,
          user_message: "Stored user request",
          assistant_message: "Stored assistant response",
          created_at: "2026-06-18T10:00:00.000Z",
        },
      ],
      total: 1,
      offset: 0,
      limit: 200,
      has_more: false,
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <Harness />
        </QueryClientProvider>,
      );
    });

    await act(async () => {
      await runtime?.loadConversation({
        id: "conv-1",
        title: "Stored session",
        messages: [],
        runtimeSessionId: "runtime-1",
        durableSessionId: "durable-1",
        phase: "idle",
        createdAt: "2026-06-18T10:00:00.000Z",
        updatedAt: "2026-06-18T10:00:00.000Z",
      });
    });

    expect(mocked.sessionsTurns).toHaveBeenCalledWith("durable-1", { limit: 200, offset: 0 });
    expect(useChatStore.getState().messages).toEqual([
      {
        id: "turn-turn-1-user",
        type: "user",
        content: "Stored user request",
      },
      {
        id: "turn-turn-1-assistant",
        type: "assistant",
        content: "Stored assistant response",
        streaming: false,
      },
    ]);
    expect(useChatStore.getState().turnArtifactsByMessageId).toEqual({});
    expect(mocked.setCreationPhase).toHaveBeenCalledWith("complete");
  });
});
