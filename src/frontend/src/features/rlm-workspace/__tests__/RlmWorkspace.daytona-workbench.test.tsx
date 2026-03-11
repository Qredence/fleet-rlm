import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { RlmWorkspace } from "@/features/rlm-workspace/RlmWorkspace";

const chatStoreState = {
  runtimeMode: "daytona_pilot" as const,
  setRuntimeMode: vi.fn(),
  daytonaRepoUrl: "",
  setDaytonaRepoUrl: vi.fn(),
  daytonaRepoRef: "main",
  setDaytonaRepoRef: vi.fn(),
  daytonaMaxDepth: 2,
  setDaytonaMaxDepth: vi.fn(),
  daytonaBatchConcurrency: 4,
  setDaytonaBatchConcurrency: vi.fn(),
};

vi.mock("@posthog/react", () => ({
  usePostHog: () => ({ capture: vi.fn() }),
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/hooks/useStickToBottom", () => ({
  useStickToBottom: () => ({
    scrollRef: null,
    contentRef: null,
    isAtBottom: true,
    scrollToBottom: vi.fn(),
  }),
}));

vi.mock("@/stores/navigationStore", () => ({
  useNavigationStore: () => ({
    sessionId: 1,
  }),
}));

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({
    navigate: vi.fn(),
  }),
}));

vi.mock("@/stores/chatHistoryStore", () => ({
  useChatHistoryStore: () => ({
    conversations: [],
    saveConversation: vi.fn(),
    loadConversation: vi.fn(),
    deleteConversation: vi.fn(),
    clearHistory: vi.fn(),
  }),
}));

vi.mock("@/features/rlm-workspace/useBackendChatRuntime", () => ({
  useBackendChatRuntime: () => ({
    messages: [{ id: "m1", type: "assistant", content: "existing chat row" }],
    turnArtifactsByMessageId: {},
    inputValue:
      "Analyze https://github.com/qredence/fleet-rlm and summarize the tracing flow.",
    setInputValue: vi.fn(),
    phase: "idle",
    isTyping: false,
    handleSubmit: vi.fn(),
    resolveHitl: vi.fn(),
    resolveClarification: vi.fn(),
    loadConversation: vi.fn(),
  }),
}));

vi.mock("@/features/settings/useRuntimeSettings", () => ({
  useRuntimeStatus: () => ({ data: { ready: true, guidance: [] } }),
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  createBackendSessionId: vi.fn(() => "test-session-id"),
}));

vi.mock("@/stores/chatStore", () => ({
  useChatStore: (selector: (state: typeof chatStoreState) => unknown) =>
    selector(chatStoreState),
}));

vi.mock("@/features/rlm-workspace/ChatMessageList", () => ({
  ChatMessageList: () => <div data-testid="chat-message-list">ChatMessageList</div>,
}));

vi.mock("@/features/rlm-workspace/daytona-workbench/DaytonaWorkbench", () => ({
  DaytonaWorkbench: () => (
    <div data-testid="daytona-workbench">DaytonaWorkbench</div>
  ),
}));

describe("RlmWorkspace Daytona workbench mode", () => {
  beforeEach(() => {
    chatStoreState.runtimeMode = "daytona_pilot";
    chatStoreState.daytonaRepoUrl = "";
    chatStoreState.daytonaRepoRef = "main";
    chatStoreState.daytonaMaxDepth = 2;
    chatStoreState.daytonaBatchConcurrency = 4;
  });

  it("renders the dedicated Daytona workbench instead of the generic chat body", () => {
    const html = renderToStaticMarkup(<RlmWorkspace />);

    expect(html).toContain("DaytonaWorkbench");
    expect(html).not.toContain("ChatMessageList");
    expect(html).toContain("Daytona run setup");
    expect(html).toContain("Daytona repository URL");
    expect(html).toContain("Detected from prompt URL");
    expect(html).toContain("Show advanced");
  });
});
