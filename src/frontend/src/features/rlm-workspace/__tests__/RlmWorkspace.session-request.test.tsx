import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { RlmWorkspace } from "@/features/rlm-workspace/RlmWorkspace";
import type { Conversation } from "@/stores/chatHistoryStore";
import { useChatHistoryStore } from "@/stores/chatHistoryStore";
import { useChatStore } from "@/stores/chatStore";
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

vi.mock("@/features/rlm-workspace/useChatRuntime", () => ({
  useChatRuntime: () => backendRuntimeState,
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

vi.mock("@/features/settings/useRuntimeSettings", () => ({
  useRuntimeStatus: () => ({ data: { ready: true, guidance: [] } }),
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  createBackendSessionId: vi.fn(() => "test-session-id"),
}));

vi.mock("@/features/rlm-workspace/ChatMessageList", () => ({
  ChatMessageList: () => <div>ChatMessageList</div>,
}));

vi.mock("@/components/shared/ConversationHistory", () => ({
  ConversationHistory: () => <div>ConversationHistory</div>,
}));

vi.mock("@/components/chat/ChatInput", () => ({
  ChatInput: () => <div>ChatInput</div>,
}));

describe("RlmWorkspace requested conversation loading", () => {
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
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectory",
      selectedFileNode: null,
      creationPhase: "idle",
      sessionId: 0,
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
    useNavigationStore.setState({ requestedConversationId: "conv-1" });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<RlmWorkspace />);
    });

    expect(backendRuntimeState.loadConversation).toHaveBeenCalledWith(conversation);
    expect(useNavigationStore.getState().requestedConversationId).toBeNull();

    act(() => {
      root.unmount();
    });
  });
});
