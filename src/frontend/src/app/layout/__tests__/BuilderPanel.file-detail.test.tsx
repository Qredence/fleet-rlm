import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BuilderPanel } from "@/app/layout/BuilderPanel";

const chatStoreState = {
  runtimeMode: "modal_chat" as const,
};

vi.mock("@/stores/navigationStore", () => ({
  useNavigationStore: () => ({
    activeNav: "volumes",
    creationPhase: "idle",
    closeCanvas: vi.fn(),
    activeFeatures: new Set(),
    selectedFileNode: {
      id: "file-1",
      name: "README.md",
      path: "/workspaces/default/README.md",
      type: "file",
      children: [],
      size: 120,
      modifiedAt: "2026-03-03T00:00:00Z",
    },
  }),
}));

vi.mock("@/stores/chatStore", () => ({
  useChatStore: (selector: (state: typeof chatStoreState) => unknown) =>
    selector(chatStoreState),
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

vi.mock("@/features/rlm-workspace/message-inspector/MessageInspectorPanel", () => ({
  MessageInspectorPanel: () => <div>MessageInspectorPanel</div>,
}));

vi.mock("@/features/rlm-workspace/run-workbench/RunWorkbench", () => ({
  RunWorkbench: () => <div>RunWorkbench</div>,
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  isSectionSupported: () => true,
  UNSUPPORTED_SECTION_REASON: "Unsupported",
  createBackendSessionId: () => "session-test",
}));

describe("BuilderPanel file detail mode", () => {
  it("renders FileDetail when a volumes file is selected", () => {
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <BuilderPanel />
      </QueryClientProvider>,
    );

    expect(html).toContain("FileDetail:README.md");
    expect(html).not.toContain("No active canvas");
    expect(html).toContain("CanvasSwitcher");
    expect(html).not.toContain("MessageInspectorPanel");
  });
});
