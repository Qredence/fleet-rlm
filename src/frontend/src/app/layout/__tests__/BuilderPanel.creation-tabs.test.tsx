import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BuilderPanel } from "@/app/layout/BuilderPanel";

vi.mock("@/hooks/useNavigation", () => ({
  useNavigation: () => ({
    activeNav: "workspace",
    creationPhase: "active",
    closeCanvas: vi.fn(),
    activeFeatures: new Set(),
    selectedFileNode: null,
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

vi.mock("@/components/domain/artifacts/ArtifactCanvas", () => ({
  ArtifactCanvas: ({
    showTabs,
    activeTab,
  }: {
    showTabs?: boolean;
    activeTab?: string;
  }) => (
    <div>
      ArtifactCanvas showTabs:{String(showTabs)} activeTab:{activeTab}
    </div>
  ),
}));

vi.mock("@/features/artifacts/CodeArtifact", () => ({
  CodeArtifact: () => <div>CodeArtifact</div>,
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

describe("BuilderPanel workspace header tabs", () => {
  it("shows artifact tabs in the header and replaces the execution dropdown switcher", () => {
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <BuilderPanel />
      </QueryClientProvider>,
    );

    expect(html).toContain('role="tablist"');
    expect(html).toContain("Graph");
    expect(html).toContain("REPL");
    expect(html).toContain("Timeline");
    expect(html).toContain("Preview");
    expect(html).not.toContain("CanvasSwitcher");
    expect(html).toContain("ArtifactCanvas showTabs:false");
    expect(html).toContain("hover:bg-white/8");
    expect(html).toContain("bg-white/12");
  });
});
