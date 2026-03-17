import { act } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { TopHeader } from "@/app/layout/TopHeader";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("lucide-react", () => ({
  PanelRight: () => <svg aria-hidden="true" />,
}));

vi.mock("@/stores/navigationStore", () => ({
  useNavigationStore: () => ({
    activeNav: "workspace",
    isCanvasOpen: false,
    toggleCanvas: vi.fn(),
  }),
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/components/ui/icon-button", () => ({
  IconButton: ({ children, className }: { children: ReactNode; className?: string }) => (
    <button type="button" className={className}>
      {children}
    </button>
  ),
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

function mountHeader() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(<TopHeader />);
  });

  return { container, root };
}

describe("TopHeader typography", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("uses reduced title typography for the workspace header", () => {
    const { container, root } = mountHeader();
    const heading = container.querySelector(".text-sm");

    expect(heading?.textContent).toBe("RLM Workspace");
    expect(heading?.className).toContain("text-sm");
    expect(heading?.className).toContain("font-medium");
    expect(heading?.className).toContain("truncate");

    act(() => {
      root.unmount();
    });
  });
});
