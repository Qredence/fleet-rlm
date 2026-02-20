/**
 * Desktop layout shell.
 *
 * Owns the resizable split-panel layout (react-resizable-panels),
 * panel animation state, and desktop-specific canvas open/close logic.
 *
 * Registers panel-aware canvas handlers with NavigationContext so that
 * any component calling `openCanvas()` / `closeCanvas()` will drive
 * the imperative panel API correctly.
 */
import { useState, useRef, useEffect } from "react";
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
  type ImperativePanelHandle,
} from "react-resizable-panels";
import { useNavigation } from "@/hooks/useNavigation";
import { TopHeader } from "@/app/layout/TopHeader";
import { ChatPanel } from "@/app/layout/ChatPanel";
import { BuilderPanel } from "@/app/layout/BuilderPanel";
import { cn } from "@/components/ui/utils";

/* ── Transition applied to both panels for smooth open/close ────── */
const PANEL_TRANSITION = "flex-grow 350ms cubic-bezier(0.4, 0, 0.2, 1)";

function DesktopShell() {
  const { isCanvasOpen, setIsCanvasOpen, registerCanvasHandlers } =
    useNavigation();

  /* ── Panel animation state ─────────────────────────────────── */
  const builderPanelRef = useRef<ImperativePanelHandle>(null);
  const [isResizing, setIsResizing] = useState(false);

  /* ── Register panel-aware canvas handlers ───────────────────── */
  useEffect(() => {
    registerCanvasHandlers({
      open: () => {
        builderPanelRef.current?.resize(50);
        setIsCanvasOpen(true);
      },
      close: () => {
        builderPanelRef.current?.collapse();
        // setIsCanvasOpen(false) will fire via onCollapse callback
      },
    });
  }, [registerCanvasHandlers, setIsCanvasOpen]);

  /* Disable CSS transition during manual handle drag for responsiveness */
  const panelStyle = {
    transition: isResizing ? "none" : PANEL_TRANSITION,
  };

  return (
    <div className="flex flex-col h-dvh bg-background">
      <TopHeader />

      <PanelGroup direction="horizontal" className="flex-1 min-h-0">
        {/* ── Chat / main content panel ─────────────────────────── */}
        <Panel id="chat" order={1} minSize={35} style={panelStyle}>
          <ChatPanel />
        </Panel>

        {/* ── Resize handle ────────────────────────────────────── */}
        <PanelResizeHandle
          className={cn(
            "relative transition-colors",
            isCanvasOpen
              ? "w-px bg-border-subtle hover:bg-accent"
              : "w-0 pointer-events-none",
          )}
          onDragging={setIsResizing}
          disabled={!isCanvasOpen}
        />

        {/* ── Builder panel (always mounted, collapses to 0) ──── */}
        <Panel
          id="builder"
          ref={builderPanelRef}
          order={2}
          collapsible
          collapsedSize={0}
          defaultSize={0}
          minSize={20}
          style={panelStyle}
          onCollapse={() => setIsCanvasOpen(false)}
          onExpand={() => setIsCanvasOpen(true)}
        >
          <div
            className={cn(
              "h-full transition-opacity duration-200",
              isCanvasOpen ? "opacity-100" : "opacity-0",
            )}
          >
            <BuilderPanel />
          </div>
        </Panel>
      </PanelGroup>
    </div>
  );
}

export { DesktopShell };
