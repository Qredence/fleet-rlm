import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { RlmWorkspace } from "@/features/rlm-workspace/RlmWorkspace";

let runtimeStatusMock: { data?: { ready: boolean; guidance?: string[] } } = {
  data: {
    ready: false,
    guidance: ["Run Runtime tests from Settings -> Runtime."],
  },
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

vi.mock("@/hooks/useNavigation", () => ({
  useNavigation: () => ({
    sessionId: 1,
  }),
}));

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({
    navigate: vi.fn(),
  }),
}));

vi.mock("@/hooks/useChatHistory", () => ({
  useChatHistory: () => ({
    conversations: [],
    saveConversation: vi.fn(),
    loadConversation: vi.fn(),
    deleteConversation: vi.fn(),
    clearHistory: vi.fn(),
  }),
}));

vi.mock("@/features/rlm-workspace/useBackendChatRuntime", () => ({
  useBackendChatRuntime: () => ({
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
}));

vi.mock("@/features/rlm-workspace/ChatMessageList", () => ({
  ChatMessageList: () => <div>ChatMessageList</div>,
}));

vi.mock("@/features/rlm-workspace/ConversationHistory", () => ({
  ConversationHistory: () => <div>ConversationHistory</div>,
}));

vi.mock("@/components/chat/ChatInput", () => ({
  ChatInput: () => <div>ChatInput</div>,
}));

describe("RlmWorkspace runtime warning", () => {
  beforeEach(() => {
    runtimeStatusMock = {
      data: {
        ready: false,
        guidance: ["Run Runtime tests from Settings -> Runtime."],
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
    runtimeStatusMock = { data: { ready: true, guidance: [] } };
    const html = renderToStaticMarkup(<RlmWorkspace />);
    expect(html).not.toContain("Runtime warning:");
  });
});
