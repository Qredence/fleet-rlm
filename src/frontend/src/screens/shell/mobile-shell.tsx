/**
 * Mobile layout shell — iOS 26 Liquid Glass aesthetic.
 *
 * Owns the vaul Drawer for the builder panel, glass tab bar placement,
 * and safe-area inset handling. All shared app state comes from
 * NavigationStore — no props drilled to children.
 */
import { Drawer } from "vaul";
import { useEffect } from "react";
import { useNavigationStore } from "@/stores/navigationStore";
import { ShellHeader } from "@/screens/shell/shell-header";
import { ShellRouteOutlet } from "@/screens/shell/shell-route-outlet";
import { ShellSidepanel } from "@/screens/shell/shell-sidepanel";
import { MobileTabBar } from "@/screens/shell/mobile-tab-bar";

function MobileShell() {
  const { isCanvasOpen, setIsCanvasOpen, registerCanvasHandlers } = useNavigationStore();

  useEffect(() => {
    registerCanvasHandlers({
      open: () => setIsCanvasOpen(true),
      close: () => setIsCanvasOpen(false),
    });

    // Cleanup handlers on unmount to prevent memory leaks/stale closures
    return () => {
      registerCanvasHandlers({
        open: () => {},
        close: () => {},
      });
    };
  }, [registerCanvasHandlers, setIsCanvasOpen]);

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background pt-[env(safe-area-inset-top,0px)]">
      {/* iOS 26 glass header: logo + action icons only */}
      <ShellHeader />

      {/* Main content area */}
      <div className="flex-1 min-h-0 min-w-0 overflow-hidden">
        <ShellRouteOutlet />
      </div>

      {/* iOS 26 Liquid Glass sheet / drawer */}
      <Drawer.Root open={isCanvasOpen} onOpenChange={setIsCanvasOpen}>
        <Drawer.Portal>
          <Drawer.Overlay className="surface-glass-overlay fixed inset-0 z-40" />
          <Drawer.Content className="surface-glass-sheet fixed bottom-0 left-0 right-0 z-50 flex max-h-[92dvh] flex-col">
            <Drawer.Title className="sr-only">Builder Panel</Drawer.Title>
            <Drawer.Description className="sr-only">
              Workspace detail and artifact view
            </Drawer.Description>
            {/* iOS 26 grab handle */}
            <div className="flex items-center justify-center py-2.5 shrink-0">
              <div className="surface-glass-handle h-1.25 w-9 rounded-full" aria-hidden="true" />
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <ShellSidepanel />
            </div>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>

      {/* iOS 26 floating Liquid Glass tab bar */}
      <MobileTabBar />
    </div>
  );
}

export { MobileShell };
