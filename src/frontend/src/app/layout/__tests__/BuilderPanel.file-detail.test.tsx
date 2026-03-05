import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BuilderPanel } from "@/app/layout/BuilderPanel";

vi.mock("@/hooks/useNavigation", () => ({
  useNavigation: () => ({
    activeNav: "taxonomy",
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

vi.mock("@/features/artifacts/CodeArtifact", () => ({
  CodeArtifact: () => <div>CodeArtifact</div>,
}));

vi.mock("@/features/artifacts/components/ArtifactCanvas", () => ({
  ArtifactCanvas: () => <div>ArtifactCanvas</div>,
}));

vi.mock("@/features/artifacts/FileDetail", () => ({
  FileDetail: ({ file }: { file: { name: string } }) => (
    <div>FileDetail:{file.name}</div>
  ),
}));

vi.mock("@/lib/rlm-api", () => ({
  isRlmCoreEnabled: () => true,
  isSectionSupported: () => true,
  UNSUPPORTED_SECTION_REASON: "Unsupported",
}));

describe("BuilderPanel file detail mode", () => {
  it("renders FileDetail when a taxonomy file is selected", () => {
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <BuilderPanel />
      </QueryClientProvider>,
    );

    expect(html).toContain("FileDetail:README.md");
    expect(html).not.toContain("No active canvas");
    expect(html).toContain("CanvasSwitcher");
  });
});
