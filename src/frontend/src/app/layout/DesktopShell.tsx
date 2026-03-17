/**
 * Desktop layout shell.
 *
 * Owns the resizable split-panel layout (react-resizable-panels),
 * panel animation state, and desktop-specific canvas open/close logic.
 *
 * Registers panel-aware canvas handlers with NavigationStore so that
 * any component calling `openCanvas()` / `closeCanvas()` will drive
 * the imperative panel API correctly.
 */
import { useState, useRef, useEffect, useCallback } from "react";
import type { GroupImperativeHandle } from "react-resizable-panels";
import {
  ResizablePanelGroup as PanelGroup,
  ResizablePanel as Panel,
  ResizableHandle as PanelResizeHandle,
} from "@/components/ui/resizable";
import { useNavigationStore } from "@/stores/navigationStore";
import { TopHeader } from "@/app/layout/TopHeader";
import { ChatPanel } from "@/app/layout/ChatPanel";
import { BuilderPanel } from "@/app/layout/BuilderPanel";
import { AppSidebar } from "@/app/layout/AppSidebar";
import { cn } from "@/lib/utils/cn";

/* ── Transition applied to both panels for smooth open/close ────── */
const PANEL_TRANSITION = "flex-grow 350ms cubic-bezier(0.4, 0, 0.2, 1)";
const OPEN_LAYOUT = { chat: 50, builder: 50 };
const CLOSED_LAYOUT = { chat: 100, builder: 0 };

function DesktopShell() {
  const { isCanvasOpen, setIsCanvasOpen, registerCanvasHandlers } = useNavigationStore();

  /* ── Sidebar state ─────────────────────────────────────────── */
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  /* ── Panel animation state ─────────────────────────────────── */
  const panelGroupRef = useRef<GroupImperativeHandle>(null);
  const [isResizing, setIsResizing] = useState(false);

  const syncPanelLayout = useCallback((open: boolean) => {
    panelGroupRef.current?.setLayout(open ? OPEN_LAYOUT : CLOSED_LAYOUT);
  }, []);

  /* ── Register panel-aware canvas handlers ───────────────────── */
  useEffect(() => {
    registerCanvasHandlers({
      open: () => {
        syncPanelLayout(true);
        setIsCanvasOpen(true);
      },
      close: () => {
        syncPanelLayout(false);
        setIsCanvasOpen(false);
      },
    });
  }, [registerCanvasHandlers, setIsCanvasOpen, syncPanelLayout]);

  useEffect(() => {
    syncPanelLayout(isCanvasOpen);
  }, [isCanvasOpen, syncPanelLayout]);

  useEffect(() => {
    if (!isResizing) {
      return undefined;
    }

    const stopResizing = () => {
      setIsResizing(false);
    };

    window.addEventListener("pointerup", stopResizing);
    window.addEventListener("pointercancel", stopResizing);

    return () => {
      window.removeEventListener("pointerup", stopResizing);
      window.removeEventListener("pointercancel", stopResizing);
    };
  }, [isResizing]);

  /* Disable CSS transition during manual handle drag for responsiveness */
  const panelStyle = {
    transition: isResizing ? "none" : PANEL_TRANSITION,
  };

  return (
    <div className="flex h-dvh bg-background overflow-hidden">
      <AppSidebar
        isCollapsed={isSidebarCollapsed}
        onToggleCollapse={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
      />
      <div className="flex flex-col flex-1 min-w-0">
        <TopHeader />

        <PanelGroup
          groupRef={panelGroupRef}
          orientation="horizontal"
          defaultLayout={isCanvasOpen ? OPEN_LAYOUT : CLOSED_LAYOUT}
          className="flex-1 min-h-0"
        >
          {/* ── Chat / main content panel ─────────────────────────── */}
          <Panel id="chat" minSize="35%" style={panelStyle}>
            <ChatPanel />
          </Panel>

          {/* ── Resize handle ────────────────────────────────────── */}
          <PanelResizeHandle
            className={cn(
              "relative transition-colors",
              isCanvasOpen ? "w-px bg-border-subtle hover:bg-accent" : "w-0 pointer-events-none",
            )}
            onPointerDown={() => setIsResizing(true)}
            disabled={!isCanvasOpen}
          />

          {/* ── Builder panel (always mounted, collapses to 0) ──── */}
          <Panel
            id="builder"
            collapsible
            collapsedSize="0%"
            minSize="20%"
            style={panelStyle}
            onResize={({ asPercentage }) => setIsCanvasOpen(asPercentage > 0)}
          >
            <div
              className={cn(
                "h-full min-h-0 overflow-hidden transition-opacity duration-200",
                isCanvasOpen ? "opacity-100" : "opacity-0",
              )}
            >
              <BuilderPanel />
            </div>
          </Panel>
        </PanelGroup>
      </div>
    </div>
  );
}

export { DesktopShell };
