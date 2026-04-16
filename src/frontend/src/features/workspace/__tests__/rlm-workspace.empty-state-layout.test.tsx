import { beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { WorkspaceScreen } from "@/features/workspace/workspace-screen";
import type { RuntimeStatusResponse } from "@/lib/rlm-api";

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

const chatStoreMockState = {
  runtimeMode: "daytona_pilot" as const,
  setRuntimeMode: vi.fn(),
  stopStreaming: vi.fn(),
};

let isMobileMock = false;
let runtimeStatusMock: {
  data?: Partial<RuntimeStatusResponse>;
} = {
  data: {
    ready: true,
    guidance: [],
    daytona: {
      configured: true,
      guidance: [],
    },
  },
};

vi.mock("@posthog/react", () => ({
  usePostHog: () => ({ capture: vi.fn() }),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => isMobileMock,
}));

vi.mock("@/hooks/use-app-navigate", () => ({
  useAppNavigate: () => ({
    navigate: vi.fn(),
  }),
}));

vi.mock("@/lib/telemetry/use-telemetry", () => ({
  useTelemetry: () => ({ capture: vi.fn() }),
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
    useWorkspace: () => backendRuntimeState,
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
  runtimeStatusQueryKey: ["runtime-status"],
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

describe("WorkspaceScreen empty-state layout", () => {
  function renderScreen() {
    return renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <WorkspaceScreen />
      </QueryClientProvider>,
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
    isMobileMock = false;
    backendRuntimeState.messages = [];
    backendRuntimeState.phase = "idle";
    backendRuntimeState.isTyping = false;
    backendRuntimeState.inputValue = "";
    runtimeStatusMock = {
      data: {
        ready: true,
        guidance: [],
        daytona: {
          configured: true,
          guidance: [],
        },
      },
    };
  });

  it("renders the desktop landing stack with hero copy and inline composer", () => {
    const html = renderScreen();

    expect(html).toContain('data-slot="workspace-landing-state"');
    expect(html).toContain("Start a conversation");
    // Suggestion action buttons for Daytona-aligned execution tasks
    expect(html).toContain("Build a feature");
    expect(html).toContain("Debug an issue");
    expect(html).toContain("Review changes");
    expect(html).toContain("Explore ideas");
    expect(html).toContain("WorkspaceComposer");
    expect(html).not.toContain("WorkspaceMessageList");
  });

  it("renders the runtime warning inside the desktop landing stack above the composer", () => {
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

    const html = renderScreen();

    const titleIndex = html.indexOf("Start a conversation");
    const warningIndex = html.indexOf("Runtime configuration required");
    const composerIndex = html.indexOf("WorkspaceComposer");

    expect(titleIndex).toBeGreaterThanOrEqual(0);
    expect(warningIndex).toBeGreaterThan(titleIndex);
    expect(composerIndex).toBeGreaterThan(warningIndex);
    expect(html).toContain("Open Runtime Settings");
  });

  it("falls back to the conversation layout as soon as the first turn is in flight", () => {
    backendRuntimeState.isTyping = true;

    const html = renderScreen();

    expect(html).toContain("WorkspaceMessageList");
    expect(html).toContain("WorkspaceComposer");
    expect(html).not.toContain('data-slot="workspace-landing-state"');
    expect(html).not.toContain("Start a conversation");
  });

  it("keeps the mobile zero-message path on the existing conversation layout", () => {
    isMobileMock = true;

    const html = renderScreen();

    expect(html).toContain("WorkspaceMessageList");
    expect(html).toContain("WorkspaceComposer");
    expect(html).not.toContain('data-slot="workspace-landing-state"');
    expect(html).not.toContain("Start a conversation");
  });
});
