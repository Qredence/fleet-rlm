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
  daytonaContextPaths: "",
  setDaytonaContextPaths: vi.fn(),
  daytonaMaxDepth: 2,
  setDaytonaMaxDepth: vi.fn(),
  daytonaBatchConcurrency: 4,
  setDaytonaBatchConcurrency: vi.fn(),
};

interface MockDaytonaWorkbenchState {
  status: "idle" | "running";
  repoUrl: string | undefined;
  contextSources: Array<{ sourceId: string; kind: string; hostPath: string }>;
}

const daytonaWorkbenchState: MockDaytonaWorkbenchState = {
  status: "idle" as "idle" | "running",
  repoUrl: undefined as string | undefined,
  contextSources: [] as Array<{ sourceId: string; kind: string; hostPath: string }>,
};

const backendRuntimeState = {
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
  useBackendChatRuntime: () => backendRuntimeState,
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

vi.mock(
  "@/features/rlm-workspace/daytona-workbench/daytonaWorkbenchStore",
  () => ({
    useDaytonaWorkbenchStore: (
      selector: (state: MockDaytonaWorkbenchState) => unknown,
    ) => selector(daytonaWorkbenchState),
  }),
);

vi.mock("@/features/rlm-workspace/ChatMessageList", () => ({
  ChatMessageList: () => <div data-testid="chat-message-list">ChatMessageList</div>,
}));

vi.mock("@/components/chat/ChatInput", () => ({
  ChatInput: ({
    value,
    canSubmit,
    onSend,
  }: {
    value: string;
    canSubmit?: boolean;
    onSend: (attachments: never[]) => void;
  }) => (
    <div data-testid="chat-input">
      <span>{value}</span>
      <button
        type="button"
        disabled={!canSubmit}
        onClick={() => onSend([])}
      >
        Send
      </button>
    </div>
  ),
}));

describe("RlmWorkspace Daytona workbench mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    chatStoreState.runtimeMode = "daytona_pilot";
    chatStoreState.daytonaRepoUrl = "";
    chatStoreState.daytonaRepoRef = "main";
    chatStoreState.daytonaContextPaths = "";
    chatStoreState.daytonaMaxDepth = 2;
    chatStoreState.daytonaBatchConcurrency = 4;
    daytonaWorkbenchState.status = "idle";
    daytonaWorkbenchState.repoUrl = undefined;
    daytonaWorkbenchState.contextSources = [];
    backendRuntimeState.messages = [
      { id: "m1", type: "assistant", content: "existing chat row" },
    ];
    backendRuntimeState.inputValue =
      "Analyze https://github.com/qredence/fleet-rlm and summarize the tracing flow.";
    backendRuntimeState.phase = "idle";
    backendRuntimeState.isTyping = false;
  });

  it("keeps the chat body visible and shows Daytona setup above the composer", () => {
    const html = renderToStaticMarkup(<RlmWorkspace />);

    expect(html).toContain("ChatMessageList");
    expect(html).toContain("Daytona source setup");
    expect(html).toContain("Edit source setup");
    expect(html).toContain("Repo");
    expect(html).toContain("Repo ready");
  });

  it("keeps the active run source mix visible while Daytona is running", () => {
    backendRuntimeState.inputValue = "";
    daytonaWorkbenchState.status = "running";
    daytonaWorkbenchState.repoUrl = "https://github.com/qredence/fleet-rlm";
    daytonaWorkbenchState.contextSources = [
      {
        sourceId: "ctx-1",
        kind: "directory",
        hostPath: "/Users/zocho/Documents/specs",
      },
    ];

    const html = renderToStaticMarkup(<RlmWorkspace />);

    expect(html).toContain("Active run context");
    expect(html).toContain("Active Daytona run is using the current source mix shown above.");
    expect(html).toContain("https://github.com/qredence/fleet-rlm");
    expect(html).toContain("1 local path");
  });
});
