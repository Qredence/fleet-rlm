import { act } from "react";
import { createRoot } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vite-plus/test";

import { WorkspaceScreen } from "@/screens/workspace/workspace-screen";
import type { Conversation } from "@/screens/workspace/model/chat-history-store";
import { useChatHistoryStore } from "@/screens/workspace/model/chat-history-store";
import { useChatStore } from "@/screens/workspace/model/chat-store";
import { useWorkspaceUiStore } from "@/screens/workspace/model/workspace-ui-store";
import { useNavigationStore } from "@/stores/navigationStore";

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

vi.mock("@/screens/workspace/hooks/use-workspace-runtime", () => ({
  useWorkspaceRuntime: () => backendRuntimeState,
}));

vi.mock("@/hooks/useStickToBottom", () => ({
  useStickToBottom: () => ({
    scrollRef: { current: null },
    contentRef: { current: null },
    isAtBottom: true,
    scrollToBottom: vi.fn(),
  }),
}));

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({ navigate: vi.fn(), navigateTo: vi.fn() }),
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/lib/telemetry/useTelemetry", () => ({
  useTelemetry: () => ({ capture: vi.fn() }),
}));

vi.mock("@/hooks/useRuntimeStatus", () => ({
  useRuntimeStatus: () => ({ data: { ready: true, guidance: [] } }),
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  createBackendSessionId: vi.fn(() => "test-session-id"),
}));

vi.mock("@/screens/workspace/components/workspace-message-list", () => ({
  WorkspaceMessageList: () => <div>WorkspaceMessageList</div>,
}));

vi.mock("@/screens/workspace/components/workspace-sidebar", () => ({
  WorkspaceSidebar: () => <div>WorkspaceSidebar</div>,
}));

vi.mock("@/screens/workspace/components/workspace-composer", () => ({
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
      activeInspectorTab: "trajectory",
      creationPhase: "idle",
      sessionRevision: 0,
      requestedConversationId: null,
    });
    useChatStore.setState({ runtimeMode: "modal_chat" });
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
      root.render(<WorkspaceScreen />);
    });

    expect(backendRuntimeState.loadConversation).toHaveBeenCalledWith(
      conversation,
    );
    expect(useWorkspaceUiStore.getState().requestedConversationId).toBeNull();

    act(() => {
      root.unmount();
    });
  });
});
