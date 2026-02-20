/**
 * Mobile layout shell — iOS 26 Liquid Glass aesthetic.
 *
 * Owns the vaul Drawer for the builder panel, glass tab bar placement,
 * and safe-area inset handling. All shared app state comes from
 * NavigationContext — no props drilled to children.
 */
import { Drawer } from "vaul";
import { useNavigation } from "../components/hooks/useNavigation";
import { TopHeader } from "./TopHeader";
import { ChatPanel } from "./ChatPanel";
import { BuilderPanel } from "./BuilderPanel";
import { MobileTabBar } from "../components/ui/mobile-tab-bar";

function MobileShell() {
  const { isCanvasOpen, setIsCanvasOpen } = useNavigation();

  return (
    <div
      className="flex flex-col h-dvh bg-background overflow-hidden"
      style={{
        paddingTop: "env(safe-area-inset-top, 0px)",
      }}
    >
      {/* iOS 26 glass header: logo + action icons only */}
      <TopHeader />

      {/* Main content area */}
      <div className="flex-1 min-h-0 min-w-0 overflow-hidden">
        <ChatPanel />
      </div>

      {/* iOS 26 Liquid Glass sheet / drawer */}
      <Drawer.Root open={isCanvasOpen} onOpenChange={setIsCanvasOpen}>
        <Drawer.Portal>
          <Drawer.Overlay
            className="fixed inset-0 z-40"
            style={{ backgroundColor: "var(--glass-overlay)" }}
          />
          <Drawer.Content
            className="fixed bottom-0 left-0 right-0 z-50 flex flex-col"
            style={{
              borderTopLeftRadius: "var(--radius-card)",
              borderTopRightRadius: "var(--radius-card)",
              maxHeight: "92dvh",
              backgroundColor: "var(--glass-sheet-bg)",
              backdropFilter: "blur(var(--glass-sheet-blur))",
              WebkitBackdropFilter: "blur(var(--glass-sheet-blur))",
              borderTop: "0.5px solid var(--glass-sheet-border)",
            }}
          >
            <Drawer.Title className="sr-only">Builder Panel</Drawer.Title>
            <Drawer.Description className="sr-only">
              Skill builder and detail view
            </Drawer.Description>
            {/* iOS 26 grab handle */}
            <div className="flex items-center justify-center py-2.5 shrink-0">
              <div
                className="w-9 h-[5px] rounded-full"
                style={{ backgroundColor: "var(--glass-sheet-handle)" }}
                aria-hidden="true"
              />
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <BuilderPanel />
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
