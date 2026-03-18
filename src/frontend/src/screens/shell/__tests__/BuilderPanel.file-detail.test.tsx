import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ShellSidepanel } from "@/screens/shell/shell-sidepanel";

const fileDetailState = {
  activeNav: "volumes" as const,
};

vi.mock("@/stores/navigationStore", () => ({
  useNavigationStore: (
    selector?: (state: { activeNav: "volumes" }) => unknown,
  ) =>
    selector
      ? selector({ activeNav: fileDetailState.activeNav })
      : { activeNav: fileDetailState.activeNav },
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

vi.mock("@/screens/volumes/volumes-canvas-panel", () => ({
  VolumesCanvasPanel: () => <div>VolumeFileDetail:README.md</div>,
}));

vi.mock("@/screens/workspace/workspace-canvas-panel", () => ({
  WorkspaceCanvasPanel: () => <div>MessageInspectorPanel</div>,
  useWorkspaceCanvasTitle: () => "Message Inspector",
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

    expect(html).toContain("File Preview");
    expect(html).toContain("VolumeFileDetail:README.md");
    expect(html).not.toContain("No active panel");
    expect(html).not.toContain("MessageInspectorPanel");
  });
});
