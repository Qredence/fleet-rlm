import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { RootLayout } from "@/features/layout";
import { useNavigationStore } from "@/stores/navigation-store";

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => true,
}));

vi.mock("@/features/layout/header", () => ({
  LayoutHeader: () => <div>LayoutHeader</div>,
}));

vi.mock("@/features/layout/app-sidebar", () => ({
  LayoutSidebar: () => <div>LayoutSidebar</div>,
}));

vi.mock("@/features/layout/route-sync", () => ({
  RouteSync: () => null,
}));

vi.mock("@/features/layout/route-outlet", () => ({
  LayoutRouteOutlet: () => <div>LayoutRouteOutlet</div>,
}));

vi.mock("@/features/layout/sidepanel", () => ({
  LayoutSidepanel: () => <div>LayoutSidepanel</div>,
}));

vi.mock("@/features/layout/mobile-tab-bar", () => ({
  MobileTabBar: () => <div>MobileTabBar</div>,
}));

vi.mock("@/features/layout/command-palette", () => ({
  CommandPalette: () => null,
}));

vi.mock("@/features/layout/login-dialog", () => ({
  LoginDialog: () => null,
}));

describe("MobileShell canvas handlers", () => {
  beforeEach(() => {
    useNavigationStore.setState({
      isCanvasOpen: true,
      activeNav: "workspace",
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
      root.render(<RootLayout />);
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
