import { describe, expect, it, vi, beforeEach } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";

import { RlmWorkspace } from "@/features/rlm-workspace/RlmWorkspace";

let runtimeStatusMock: {
  data?: {
    ready: boolean;
    guidance?: string[];
    daytona?: {
      configured?: boolean;
      guidance?: string[];
    };
  };
} = {
  data: {
    ready: false,
    guidance: ["Run Runtime tests from Settings -> Runtime."],
    daytona: {
      configured: false,
      guidance: [],
    },
  },
};

const chatStoreMockState = {
  runtimeMode: "modal_chat" as "modal_chat" | "daytona_pilot",
  setRuntimeMode: vi.fn(),
};

vi.mock("@posthog/react", () => ({
  usePostHog: () => ({ capture: vi.fn() }),
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/hooks/useStickToBottom", () => ({
  useStickToBottom: () => ({ scrollRef: null, contentRef: null }),
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

vi.mock("@/stores/chatStore", () => ({
  useChatStore: (selector: (state: typeof chatStoreMockState) => unknown) =>
    selector(chatStoreMockState),
}));

vi.mock("@/features/rlm-workspace/useChatRuntime", () => ({
  useChatRuntime: () => ({
    messages: [],
    inputValue: "",
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
  useRuntimeStatus: () => runtimeStatusMock,
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

describe("RlmWorkspace runtime warning", () => {
  beforeEach(() => {
    chatStoreMockState.runtimeMode = "modal_chat";
    chatStoreMockState.setRuntimeMode.mockReset();
    runtimeStatusMock = {
      data: {
        ready: false,
        guidance: ["Run Runtime tests from Settings -> Runtime."],
        daytona: {
          configured: false,
          guidance: [],
        },
      },
    };
  });

  it("renders warning banner when runtime status is unhealthy", () => {
    const html = renderToStaticMarkup(<RlmWorkspace />);
    expect(html).toContain('data-slot="alert"');
    expect(html).toContain("Runtime warning");
    expect(html).toContain("Run Runtime tests from Settings -&gt; Runtime.");
    expect(html).toContain("Open Runtime Settings");
  });

  it("omits warning banner when runtime status is healthy", () => {
    runtimeStatusMock = {
      data: {
        ready: true,
        guidance: [],
        daytona: { configured: true, guidance: [] },
      },
    };
    const html = renderToStaticMarkup(<RlmWorkspace />);
    expect(html).not.toContain("Runtime warning:");
  });

  it("renders Daytona guidance when Daytona mode is selected", () => {
    chatStoreMockState.runtimeMode = "daytona_pilot";
    runtimeStatusMock = {
      data: {
        ready: true,
        guidance: [],
        daytona: {
          configured: false,
          guidance: ["Missing DAYTONA_API_KEY. Set DAYTONA_API_KEY before using Daytona commands."],
        },
      },
    };

    const html = renderToStaticMarkup(<RlmWorkspace />);

    expect(html).toContain("Daytona setup required");
    expect(html).toContain("Missing DAYTONA_API_KEY");
  });
});
