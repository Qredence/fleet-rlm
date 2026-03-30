import { act } from "react";
import { createRoot } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vite-plus/test";

import { RootLayout } from "@/screens/shell/app-shell-screen";
import { useNavigationStore } from "@/stores/navigation-store";

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => true,
}));

vi.mock("@/screens/shell/shell-header", () => ({
  ShellHeader: () => <div>ShellHeader</div>,
}));

vi.mock("@/screens/shell/app-sidebar", () => ({
  AppSidebar: () => <div>AppSidebar</div>,
}));

vi.mock("@/app/shell/route-sync", () => ({
  RouteSync: () => null,
}));

vi.mock("@/app/shell/shell-route-outlet", () => ({
  ShellRouteOutlet: () => <div>ShellRouteOutlet</div>,
}));

vi.mock("@/screens/shell/shell-sidepanel", () => ({
  ShellSidepanel: () => <div>ShellSidepanel</div>,
}));

vi.mock("@/app/shell/mobile-tab-bar", () => ({
  MobileTabBar: () => <div>MobileTabBar</div>,
}));

vi.mock("@/app/shell/command-palette", () => ({
  CommandPalette: () => null,
}));

vi.mock("@/app/shell/login-dialog", () => ({
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
