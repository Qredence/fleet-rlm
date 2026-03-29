import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ShellSidepanel } from "@/screens/shell/shell-sidepanel";
import { useNavigationStore } from "@/stores/navigation-store";

vi.mock("@/hooks/use-app-navigate", () => ({
  useAppNavigate: () => ({ navigateTo: vi.fn() }),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/components/error-boundary", () => ({
  ErrorBoundary: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

const workspaceCanvas = vi.fn(() => <div>MessageInspectorPanel</div>);
vi.mock("@/screens/workspace/workspace-canvas-panel", () => ({
  WorkspaceCanvasPanel: () => workspaceCanvas(),
  useWorkspaceCanvasTitle: () => "Canvas",
  WorkspaceCanvasUnavailablePanel: () => <div>WorkspaceUnavailable</div>,
}));

vi.mock("@/screens/volumes/volumes-canvas-panel", () => ({
  VolumesCanvasPanel: () => <div>VolumeCanvas</div>,
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  isSectionSupported: () => true,
  UNSUPPORTED_SECTION_REASON: "Unsupported",
}));

describe("ShellSidepanel workspace inspector", () => {
  it("keeps the builder panel shell scrollable", () => {
    useNavigationStore.setState({
      activeNav: "workspace",
      isCanvasOpen: false,
    });
    workspaceCanvas.mockReturnValueOnce(<div>MessageInspectorPanel</div>);
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <ShellSidepanel />
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

  it("shows the message inspector header", () => {
    useNavigationStore.setState({
      activeNav: "workspace",
      isCanvasOpen: false,
    });
    workspaceCanvas.mockReturnValueOnce(<div>MessageInspectorPanel</div>);
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <ShellSidepanel />
      </QueryClientProvider>,
    );

    expect(html).toContain("Canvas");
    expect(html).toContain("MessageInspectorPanel");
    expect(html).not.toContain("Support rail");
  });

  it("renders the Daytona workbench when Daytona runtime mode is active", () => {
    useNavigationStore.setState({
      activeNav: "workspace",
      isCanvasOpen: false,
    });
    workspaceCanvas.mockReturnValueOnce(<div>RunWorkbench</div>);
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <ShellSidepanel />
      </QueryClientProvider>,
    );

    expect(html).toContain("Canvas");
    expect(html).toContain("RunWorkbench");
    expect(html).not.toContain("Support rail");
    expect(html).not.toContain("MessageInspectorPanel");
  });
});
