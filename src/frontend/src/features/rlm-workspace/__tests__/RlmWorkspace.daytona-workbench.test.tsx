import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { RlmWorkspace } from "@/features/rlm-workspace/RlmWorkspace";

const chatStoreState = {
  runtimeMode: "daytona_pilot" as const,
  setRuntimeMode: vi.fn(),
  sourceRepoUrl: "",
  setSourceRepoUrl: vi.fn(),
  sourceRepoRef: "main",
  setSourceRepoRef: vi.fn(),
  sourceContextPaths: "",
  setSourceContextPaths: vi.fn(),
  sourceMaxDepth: 2,
  setSourceMaxDepth: vi.fn(),
  sourceBatchConcurrency: 4,
  setSourceBatchConcurrency: vi.fn(),
};

interface MockRunWorkbenchState {
  status: "idle" | "running";
  repoUrl: string | undefined;
  contextSources: Array<{ sourceId: string; kind: string; hostPath: string }>;
}

const runWorkbenchState: MockRunWorkbenchState = {
  status: "idle" as "idle" | "running",
  repoUrl: undefined as string | undefined,
  contextSources: [] as Array<{
    sourceId: string;
    kind: string;
    hostPath: string;
  }>,
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

vi.mock("@/features/rlm-workspace/useBackendChatRuntime", () => ({
  useBackendChatRuntime: () => backendRuntimeState,
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

vi.mock("@/stores/navigationStore", () => ({
  useNavigationStore: () => ({ sessionId: 1 }),
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

vi.mock("@/stores/chatStore", () => ({
  useChatStore: (selector: (state: typeof chatStoreState) => unknown) =>
    selector(chatStoreState),
}));

vi.mock("@/features/rlm-workspace/run-workbench/runWorkbenchStore", () => ({
  useRunWorkbenchStore: (selector: (state: MockRunWorkbenchState) => unknown) =>
    selector(runWorkbenchState),
}));

vi.mock("@/features/rlm-workspace/ChatMessageList", () => ({
  ChatMessageList: () => (
    <div data-testid="chat-message-list">ChatMessageList</div>
  ),
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
      <button type="button" disabled={!canSubmit} onClick={() => onSend([])}>
        Send
      </button>
    </div>
  ),
}));

describe("RlmWorkspace run workbench mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    chatStoreState.runtimeMode = "daytona_pilot";
    chatStoreState.sourceRepoUrl = "";
    chatStoreState.sourceRepoRef = "main";
    chatStoreState.sourceContextPaths = "";
    chatStoreState.sourceMaxDepth = 2;
    chatStoreState.sourceBatchConcurrency = 4;
    runWorkbenchState.status = "idle";
    runWorkbenchState.repoUrl = undefined;
    runWorkbenchState.contextSources = [];
    backendRuntimeState.messages = [
      { id: "m1", type: "assistant", content: "existing chat row" },
    ];
    backendRuntimeState.inputValue =
      "Analyze https://github.com/qredence/fleet-rlm and summarize the tracing flow.";
    backendRuntimeState.phase = "idle";
    backendRuntimeState.isTyping = false;
  });

  it("keeps the chat body visible and shows source setup above the composer", () => {
    const html = renderToStaticMarkup(<RlmWorkspace />);

    expect(html).toContain("ChatMessageList");
    expect(html).toContain("Source setup");
    expect(html).toContain("Edit source setup");
    expect(html).toContain("Repo");
    expect(html).toContain("Repo ready");
  });

  it("keeps the active run source mix visible while the runtime is running", () => {
    backendRuntimeState.inputValue = "";
    runWorkbenchState.status = "running";
    runWorkbenchState.repoUrl = "https://github.com/qredence/fleet-rlm";
    runWorkbenchState.contextSources = [
      {
        sourceId: "ctx-1",
        kind: "directory",
        hostPath: "/Users/zocho/Documents/specs",
      },
    ];

    const html = renderToStaticMarkup(<RlmWorkspace />);

    expect(html).toContain("Active run context");
    expect(html).toContain(
      "The active run is using the current source mix shown above.",
    );
    expect(html).toContain("https://github.com/qredence/fleet-rlm");
    expect(html).toContain("1 local path");
  });
});
