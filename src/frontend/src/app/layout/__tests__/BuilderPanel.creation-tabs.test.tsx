import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BuilderPanel } from "@/app/layout/BuilderPanel";

const chatStoreState = {
  runtimeMode: "modal_chat" as "modal_chat" | "daytona_pilot",
};

const runWorkbenchStoreState = {
  status: "completed",
  repoUrl: "https://github.com/qredence/fleet-rlm",
  contextSources: [
    {
      sourceId: "ctx-1",
      kind: "directory",
      hostPath: "/Users/zocho/Documents/specs",
    },
  ],
  daytonaMode: "host_loop_rlm",
  summary: {
    terminationReason: "completed",
  },
};

vi.mock("@/stores/navigationStore", () => ({
  useNavigationStore: () => ({
    activeNav: "workspace",
    creationPhase: "active",
    closeCanvas: vi.fn(),
    selectedFileNode: null,
  }),
}));

vi.mock("@/stores/chatStore", () => ({
  useChatStore: (selector: (state: typeof chatStoreState) => unknown) =>
    selector(chatStoreState),
}));

vi.mock("@/features/rlm-workspace/run-workbench/runWorkbenchStore", () => ({
  useRunWorkbenchStore: () => runWorkbenchStoreState,
}));

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({ navigateTo: vi.fn() }),
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/components/shared/ErrorBoundary", () => ({
  ErrorBoundary: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/ui/icon-button", () => ({
  IconButton: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/features/artifacts/CanvasSwitcher", () => ({
  CanvasSwitcher: () => <div>CanvasSwitcher</div>,
}));

vi.mock("@/features/artifacts/FileDetail", () => ({
  FileDetail: ({ file }: { file: { name: string } }) => (
    <div>FileDetail:{file.name}</div>
  ),
}));

vi.mock(
  "@/features/rlm-workspace/message-inspector/MessageInspectorPanel",
  () => ({
    MessageInspectorPanel: () => <div>MessageInspectorPanel</div>,
  }),
);

vi.mock("@/features/rlm-workspace/run-workbench/RunWorkbench", () => ({
  RunWorkbench: () => <div>RunWorkbench</div>,
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  isSectionSupported: () => true,
  UNSUPPORTED_SECTION_REASON: "Unsupported",
  createBackendSessionId: () => "session-test",
}));

describe("BuilderPanel workspace inspector", () => {
  it("keeps the builder panel shell scrollable", () => {
    chatStoreState.runtimeMode = "modal_chat";
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <BuilderPanel />
      </QueryClientProvider>,
    );

    const container = document.createElement("div");
    container.innerHTML = html;

    const root = container.firstElementChild as HTMLElement | null;
    const content = root?.children.item(1) as HTMLElement | null;

    expect(root).not.toBeNull();
    expect(root?.classList.contains("min-h-0")).toBe(true);
    expect(root?.classList.contains("overflow-hidden")).toBe(false);

    expect(content).not.toBeNull();
    expect(content?.classList.contains("min-h-0")).toBe(true);
    expect(content?.classList.contains("flex-1")).toBe(true);
    expect(content?.classList.contains("overflow-auto")).toBe(true);
  });

  it("shows the message inspector header and hides the legacy canvas switcher", () => {
    chatStoreState.runtimeMode = "modal_chat";
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <BuilderPanel />
      </QueryClientProvider>,
    );

    expect(html).toContain("Message Inspector");
    expect(html).toContain("MessageInspectorPanel");
    expect(html).not.toContain("Support rail");
    expect(html).not.toContain("CanvasSwitcher");
    expect(html).not.toContain("ArtifactCanvas");
  });

  it("renders the Daytona workbench when Daytona runtime mode is active", () => {
    chatStoreState.runtimeMode = "daytona_pilot";
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <BuilderPanel />
      </QueryClientProvider>,
    );

    expect(html).toContain("Message Inspector");
    expect(html).toContain("RunWorkbench");
    expect(html).not.toContain("Support rail");
    expect(html).not.toContain("MessageInspectorPanel");
  });
});
