import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ShellSidepanel } from "@/features/layout/sidepanel";

const fileDetailState = {
  activeNav: "volumes" as const,
};

vi.mock("@/stores/navigation-store", () => ({
  useNavigationStore: (selector?: (state: { activeNav: "volumes" }) => unknown) =>
    selector
      ? selector({ activeNav: fileDetailState.activeNav })
      : { activeNav: fileDetailState.activeNav },
}));

vi.mock("@/hooks/use-app-navigate", () => ({
  useAppNavigate: () => ({ navigateTo: vi.fn() }),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/components/patterns/error-boundary", () => ({
  ErrorBoundary: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/screens/volumes/volumes-canvas-panel", () => ({
  VolumesCanvasPanel: () => <div>VolumeFileDetail:README.md</div>,
}));

vi.mock("@/screens/workspace/workspace-canvas-panel", () => ({
  WorkspaceCanvasPanel: () => <div>MessageInspectorPanel</div>,
  useWorkspaceCanvasTitle: () => "Canvas",
  WorkspaceCanvasUnavailablePanel: () => <div>WorkspaceUnavailable</div>,
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  isSectionSupported: () => true,
  UNSUPPORTED_SECTION_REASON: "Unsupported",
}));

describe("ShellSidepanel file detail mode", () => {
  it("renders VolumeFileDetail when a volumes file is selected", () => {
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <ShellSidepanel />
      </QueryClientProvider>,
    );

    expect(html).toContain("Preview");
    expect(html).toContain("VolumeFileDetail:README.md");
    expect(html).not.toContain("No active panel");
    expect(html).not.toContain("MessageInspectorPanel");
  });
});
