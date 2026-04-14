import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { WorkspaceScreen } from "@/features/workspace/workspace-screen";
import type { Conversation } from "@/features/workspace/use-workspace";
import { useChatHistoryStore } from "@/features/workspace/use-workspace";
import { useChatStore } from "@/features/workspace/use-workspace";
import { useWorkspaceUiStore } from "@/features/workspace/use-workspace";
import { useNavigationStore } from "@/stores/navigation-store";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const backendRuntimeState = {
  messages: [] as Array<{
    id: string;
    type: "assistant" | "user";
    content: string;
  }>,
  turnArtifactsByMessageId: {},
  inputValue: "",
  setInputValue: vi.fn(),
  phase: "idle" as const,
  isTyping: false,
  handleSubmit: vi.fn(),
  resolveHitl: vi.fn(),
  resolveClarification: vi.fn(),
  loadConversation: vi.fn(),
};

vi.mock("@/features/workspace/use-workspace", async () => {
  const actual = await vi.importActual<typeof import("@/features/workspace/use-workspace")>(
    "@/features/workspace/use-workspace",
  );

  return {
    ...actual,
    useWorkspace: () => backendRuntimeState,
    useRunWorkbenchStore: (
      selector: (state: { status: "idle"; activity: []; iterations: []; callbacks: [] }) => unknown,
    ) =>
      selector({
        status: "idle",
        activity: [],
        iterations: [],
        callbacks: [],
      }),
  } as unknown as typeof actual;
});

vi.mock("@/hooks/use-app-navigate", () => ({
  useAppNavigate: () => ({ navigate: vi.fn(), navigateTo: vi.fn() }),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/lib/telemetry/use-telemetry", () => ({
  useTelemetry: () => ({ capture: vi.fn() }),
}));

vi.mock("@/hooks/use-runtime-status", () => ({
  useRuntimeStatus: () => ({ data: { ready: true, guidance: [] } }),
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  createBackendSessionId: vi.fn(() => "test-session-id"),
}));

vi.mock("@/features/workspace/ui/transcript/workspace-message-list", () => ({
  WorkspaceMessageList: () => <div>WorkspaceMessageList</div>,
}));

vi.mock("@/features/workspace/ui/workspace-composer", () => ({
  WorkspaceComposer: () => <div>WorkspaceComposer</div>,
}));

describe("WorkspaceScreen requested conversation loading", () => {
  beforeEach(() => {
    backendRuntimeState.messages = [];
    backendRuntimeState.inputValue = "";
    backendRuntimeState.setInputValue.mockReset();
    backendRuntimeState.handleSubmit.mockReset();
    backendRuntimeState.resolveHitl.mockReset();
    backendRuntimeState.resolveClarification.mockReset();
    backendRuntimeState.loadConversation.mockReset();

    try {
      localStorage.clear();
    } catch {
      // Some test workers replace storage with a spy object that omits .clear().
    }
    useChatHistoryStore.setState({ conversations: [] });
    useNavigationStore.setState({
      activeNav: "workspace",
      isCanvasOpen: false,
    });
    useWorkspaceUiStore.setState({
      selectedAssistantTurnId: null,
      activeInspectorTab: "message",
      creationPhase: "idle",
      sessionRevision: 0,
      requestedConversationId: null,
    });
    useChatStore.setState({ runtimeMode: "daytona_pilot" });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("consumes a requested conversation id and loads the saved conversation", () => {
    const conversation: Conversation = {
      id: "conv-1",
      title: "Recovered session",
      messages: [
        {
          id: "assistant-1",
          type: "assistant",
          content: "Recovered answer",
          streaming: false,
        },
      ],
      phase: "complete",
      createdAt: "2026-03-16T10:00:00.000Z",
      updatedAt: "2026-03-16T12:00:00.000Z",
    };

    useChatHistoryStore.setState({ conversations: [conversation] });
    useWorkspaceUiStore.setState({ requestedConversationId: "conv-1" });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <WorkspaceScreen />
        </QueryClientProvider>,
      );
    });

    expect(backendRuntimeState.loadConversation).toHaveBeenCalledWith(conversation);
    expect(useWorkspaceUiStore.getState().requestedConversationId).toBeNull();

    act(() => {
      root.unmount();
    });
  });
});
