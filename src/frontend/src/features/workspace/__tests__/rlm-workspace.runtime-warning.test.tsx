import { describe, expect, it, vi, beforeEach } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { WorkspaceScreen } from "@/features/workspace/workspace-screen";

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
  runtimeMode: "daytona_pilot" as const,
  setRuntimeMode: vi.fn(),
};

vi.mock("@posthog/react", () => ({
  usePostHog: () => ({ capture: vi.fn() }),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/hooks/use-app-navigate", () => ({
  useAppNavigate: () => ({
    navigate: vi.fn(),
  }),
}));

vi.mock("@/features/workspace/use-workspace", async () => {
  const actual = await vi.importActual<typeof import("@/features/workspace/use-workspace")>(
    "@/features/workspace/use-workspace",
  );

  return {
    ...actual,
    useChatHistoryStore: () => ({
      conversations: [],
      saveConversation: vi.fn(),
      loadConversation: vi.fn(),
      deleteConversation: vi.fn(),
      clearHistory: vi.fn(),
    }),
    useChatStore: (selector: (state: typeof chatStoreMockState) => unknown) =>
      selector(chatStoreMockState),
    useWorkspace: () => ({
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

vi.mock("@/hooks/use-runtime-status", () => ({
  useRuntimeStatus: () => runtimeStatusMock,
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

describe("WorkspaceScreen runtime warning", () => {
  function renderScreen() {
    return renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <WorkspaceScreen />
      </QueryClientProvider>,
    );
  }

  beforeEach(() => {
    chatStoreMockState.runtimeMode = "daytona_pilot";
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
    const html = renderScreen();
    expect(html).toContain('data-slot="alert"');
    // Updated warning title for Phase 19
    expect(html).toContain("Sandbox configuration needed");
    // Guidance is now shown in a list format
    expect(html).toContain("Run Runtime tests from Settings");
    expect(html).toContain("Configure Sandbox");
  });

  it("omits warning banner when runtime status is healthy", () => {
    runtimeStatusMock = {
      data: {
        ready: true,
        guidance: [],
        daytona: { configured: true, guidance: [] },
      },
    };
    const html = renderScreen();
    expect(html).not.toContain("Sandbox configuration needed");
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

    const html = renderScreen();

    // Updated warning title for Phase 19
    expect(html).toContain("Sandbox configuration needed");
    expect(html).toContain("Missing DAYTONA_API_KEY");
  });
});
