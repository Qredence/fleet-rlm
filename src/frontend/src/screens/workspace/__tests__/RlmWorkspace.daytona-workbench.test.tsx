import { beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { WorkspaceScreen } from "@/screens/workspace/workspace-screen";

const chatStoreState = {
  runtimeMode: "daytona_pilot" as const,
  setRuntimeMode: vi.fn(),
};

const backendRuntimeState = {
  messages: [{ id: "m1", type: "assistant", content: "existing chat row" }],
  turnArtifactsByMessageId: {},
  inputValue: "Analyze https://github.com/qredence/fleet-rlm and summarize the tracing flow.",
  setInputValue: vi.fn(),
  phase: "idle",
  isTyping: false,
  handleSubmit: vi.fn(),
  resolveHitl: vi.fn(),
  resolveClarification: vi.fn(),
  loadConversation: vi.fn(),
};

let capturedOnSend: ((attachments: never[]) => void) | null = null;

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

vi.mock("@/screens/workspace/model/chat-history-store", () => ({
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

vi.mock("@/hooks/useRuntimeStatus", () => ({
  useRuntimeStatus: () => ({ data: { ready: true, guidance: [] } }),
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  createBackendSessionId: vi.fn(() => "test-session-id"),
}));

vi.mock("@/screens/workspace/model/chat-store", () => ({
  useChatStore: (selector: (state: typeof chatStoreState) => unknown) => selector(chatStoreState),
}));

vi.mock("@/screens/workspace/model/run-workbench-store", () => ({
  useRunWorkbenchStore: (
    selector: (state: { status: "idle"; activity: []; iterations: []; callbacks: [] }) => unknown,
  ) =>
    selector({
      status: "idle",
      activity: [],
      iterations: [],
      callbacks: [],
    }),
}));

vi.mock("@/screens/workspace/components/workspace-message-list", () => ({
  WorkspaceMessageList: () => <div data-testid="chat-message-list">WorkspaceMessageList</div>,
}));

vi.mock("@/screens/workspace/components/workspace-composer", () => ({
  WorkspaceComposer: ({
    value,
    canSubmit,
    onSend,
  }: {
    value: string;
    canSubmit?: boolean;
    onSend: (attachments: never[]) => void;
  }) => {
    capturedOnSend = onSend;
    return (
      <div data-testid="chat-input">
        <span>{value}</span>
        <button type="button" disabled={!canSubmit} onClick={() => onSend([])}>
          Send
        </button>
      </div>
    );
  },
}));

describe("WorkspaceScreen run workbench mode", () => {
  function renderScreen() {
    return renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <WorkspaceScreen />
      </QueryClientProvider>,
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
    capturedOnSend = null;
    chatStoreState.runtimeMode = "daytona_pilot";
    backendRuntimeState.messages = [{ id: "m1", type: "assistant", content: "existing chat row" }];
    backendRuntimeState.inputValue =
      "Analyze https://github.com/qredence/fleet-rlm and summarize the tracing flow.";
    backendRuntimeState.phase = "idle";
    backendRuntimeState.isTyping = false;
  });

  it("keeps the chat body visible and shows source setup above the composer", () => {
    const html = renderScreen();

    expect(html).toContain("WorkspaceMessageList");
    expect(html).toContain(
      "Analyze https://github.com/qredence/fleet-rlm and summarize the tracing flow.",
    );
    expect(html).not.toContain("Source setup");
    expect(html).not.toContain("Edit source setup");
  });

  it("still renders the chat composer without Daytona setup gating", () => {
    const html = renderScreen();

    expect(html).toContain("Send");
    expect(html).not.toContain("Repository URL");
    expect(html).not.toContain("Context paths");
  });

  it("infers Daytona repo and local context from the prompt on submit", () => {
    backendRuntimeState.inputValue =
      "Analyze https://github.com/qredence/fleet-rlm/tree/main with /Users/zocho/Documents/spec.pdf.";

    renderScreen();
    expect(capturedOnSend).not.toBeNull();

    capturedOnSend?.([]);

    expect(backendRuntimeState.handleSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        runtimeMode: "daytona_pilot",
        repoUrl: "https://github.com/qredence/fleet-rlm",
        repoRef: "main",
        contextPaths: ["/Users/zocho/Documents/spec.pdf"],
      }),
    );
  });
});
