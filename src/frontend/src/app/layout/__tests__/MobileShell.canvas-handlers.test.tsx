import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { MobileShell } from "@/app/layout/MobileShell";
import { useNavigationStore } from "@/stores/navigationStore";

vi.mock("vaul", () => ({
  Drawer: {
    Root: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Overlay: () => <div data-testid="drawer-overlay" />,
    Content: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="drawer-content">{children}</div>
    ),
    Title: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Description: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  },
}));

vi.mock("@/app/layout/TopHeader", () => ({
  TopHeader: () => <div>TopHeader</div>,
}));

vi.mock("@/app/layout/ChatPanel", () => ({
  ChatPanel: () => <div>ChatPanel</div>,
}));

vi.mock("@/app/layout/BuilderPanel", () => ({
  BuilderPanel: () => <div>BuilderPanel</div>,
}));

vi.mock("@/features/shell/MobileTabBar", () => ({
  MobileTabBar: () => <div>MobileTabBar</div>,
}));

describe("MobileShell canvas handlers", () => {
  beforeEach(() => {
    useNavigationStore.setState({
      isCanvasOpen: true,
      activeNav: "workspace",
      selectedFileNode: null,
      creationPhase: "idle",
      sessionId: 0,
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("re-registers mobile canvas handlers so closeCanvas closes the drawer", () => {
    const staleClose = vi.fn();
    useNavigationStore.getState().registerCanvasHandlers({
      open: vi.fn(),
      close: staleClose,
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<MobileShell />);
    });

    act(() => {
      useNavigationStore.getState().closeCanvas();
    });

    expect(staleClose).not.toHaveBeenCalled();
    expect(useNavigationStore.getState().isCanvasOpen).toBe(false);

    act(() => {
      root.unmount();
    });
  });
});
