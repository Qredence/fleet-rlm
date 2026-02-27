import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { SkillCreationFlow } from "@/app/pages/skill-creation/SkillCreationFlow";

let runtimeStatusMock: { data?: { ready: boolean; guidance?: string[] } } = {
  data: {
    ready: false,
    guidance: ["Run Runtime tests from Settings -> Runtime."],
  },
};

vi.mock("@posthog/react", () => ({
  usePostHog: () => ({ capture: vi.fn() }),
}));

vi.mock("@/components/ui/use-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/hooks/useStickToBottom", () => ({
  useStickToBottom: () => ({ scrollRef: null, contentRef: null }),
}));

vi.mock("@/hooks/useNavigation", () => ({
  useNavigation: () => ({
    activeFeatures: [],
    toggleFeature: vi.fn(),
    promptMode: "default",
    setPromptMode: vi.fn(),
    selectedPromptSkills: [],
    togglePromptSkill: vi.fn(),
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

vi.mock("@/app/pages/skill-creation/useBackendChatRuntime", () => ({
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

vi.mock("@/app/pages/skill-creation/ChatMessageList", () => ({
  ChatMessageList: () => <div>ChatMessageList</div>,
}));

vi.mock("@/features/ConversationHistory", () => ({
  ConversationHistory: () => <div>ConversationHistory</div>,
}));

vi.mock("@/components/ui/prompt-input", () => ({
  PromptInput: () => <div>PromptInput</div>,
}));

describe("SkillCreationFlow runtime warning", () => {
  beforeEach(() => {
    runtimeStatusMock = {
      data: {
        ready: false,
        guidance: ["Run Runtime tests from Settings -> Runtime."],
      },
    };
  });

  it("renders warning banner when runtime status is unhealthy", () => {
    const html = renderToStaticMarkup(<SkillCreationFlow />);
    expect(html).toContain("Runtime warning:");
    expect(html).toContain("Run Runtime tests from Settings -&gt; Runtime.");
    expect(html).toContain("Open Runtime Settings");
  });

  it("omits warning banner when runtime status is healthy", () => {
    runtimeStatusMock = { data: { ready: true, guidance: [] } };
    const html = renderToStaticMarkup(<SkillCreationFlow />);
    expect(html).not.toContain("Runtime warning:");
  });
});
