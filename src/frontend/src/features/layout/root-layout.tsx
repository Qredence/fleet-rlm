import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { GroupImperativeHandle } from "react-resizable-panels";

import { AppProviders } from "@/app/providers";
import { Toaster } from "@/components/ui/sonner";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { cn } from "@/lib/utils";
import {
  OPEN_SETTINGS_EVENT,
  type OpenSettingsEventDetail,
} from "@/features/settings/settings-events";
import type { SettingsSection } from "@/features/settings/settings-screen";
import { useNavigationStore } from "@/stores/navigation-store";

import { LayoutSidebar } from "./app-sidebar";
import { CommandPalette } from "./command-palette";
import { LayoutHeader } from "./header";
import { LoginDialog } from "./login-dialog";
import { MobileTabBar } from "./mobile-tab-bar";
import { getLayoutPanelMeta } from "./panel-meta";
import { RouteSync } from "./route-sync";
import { LayoutRouteOutlet } from "./route-outlet";
import { SettingsDialog } from "./settings-dialog";
import { LayoutSidepanel } from "./sidepanel";

const PANEL_TRANSITION = "flex-grow 350ms cubic-bezier(0.4, 0, 0.2, 1)";
const OPEN_LAYOUT = { content: 68, canvas: 32 };
const CLOSED_LAYOUT = { content: 100, canvas: 0 };

type OpenLoginEventDetail = {
  returnFocusTarget?: HTMLElement | null;
};

function AppLayout() {
  const isMobile = useIsMobile();
  const [cmdOpen, setCmdOpen] = useState(false);
  const { registerCommandPaletteHandlers } = useNavigationStore();
  const [loginOpen, setLoginOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection | undefined>(undefined);
  const loginReturnFocusRef = useRef<HTMLElement | null>(null);
  const settingsReturnFocusRef = useRef<HTMLElement | null>(null);
  const panelGroupRef = useRef<GroupImperativeHandle>(null);
  const [isResizing, setIsResizing] = useState(false);
  const { activeNav, isCanvasOpen, setIsCanvasOpen, registerCanvasHandlers } = useNavigationStore();
  const panelMeta = getLayoutPanelMeta(activeNav);

  useEffect(() => {
    registerCommandPaletteHandlers({ open: () => setCmdOpen(true) });
  }, [registerCommandPaletteHandlers]);

  useEffect(() => {
    const handleOpenLogin = (event: Event) => {
      const customEvent = event as CustomEvent<OpenLoginEventDetail>;
      loginReturnFocusRef.current = customEvent.detail?.returnFocusTarget ?? null;
      setLoginOpen(true);
      customEvent.preventDefault();
    };

    document.addEventListener("open-login", handleOpenLogin);
    return () => {
      document.removeEventListener("open-login", handleOpenLogin);
    };
  }, []);

  useEffect(() => {
    const handleOpenSettings = (event: Event) => {
      const customEvent = event as CustomEvent<OpenSettingsEventDetail>;
      settingsReturnFocusRef.current = customEvent.detail?.returnFocusTarget ?? null;
      setSettingsSection(customEvent.detail?.section);
      setSettingsOpen(true);
      customEvent.preventDefault();
    };

    document.addEventListener(OPEN_SETTINGS_EVENT, handleOpenSettings);
    return () => {
      document.removeEventListener(OPEN_SETTINGS_EVENT, handleOpenSettings);
    };
  }, []);

  useEffect(() => {
    const openCanvas = () => {
      if (isMobile) {
        setIsCanvasOpen(true);
        return;
      }
      panelGroupRef.current?.setLayout(OPEN_LAYOUT);
      setIsCanvasOpen(true);
    };

    const closeCanvas = () => {
      if (isMobile) {
        setIsCanvasOpen(false);
        return;
      }
      panelGroupRef.current?.setLayout(CLOSED_LAYOUT);
      setIsCanvasOpen(false);
    };

    registerCanvasHandlers({
      open: openCanvas,
      close: closeCanvas,
    });
  }, [isMobile, registerCanvasHandlers, setIsCanvasOpen]);

  useEffect(() => {
    if (isMobile) {
      return;
    }
    panelGroupRef.current?.setLayout(isCanvasOpen ? OPEN_LAYOUT : CLOSED_LAYOUT);
  }, [isCanvasOpen, isMobile]);

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

  const panelStyle = {
    transition: isResizing ? "none" : PANEL_TRANSITION,
  };

  return (
    <>
      <RouteSync />
      <SidebarProvider
        defaultOpen
        style={
          {
            "--sidebar-width": "17.5rem",
            "--sidebar-width-icon": "3.5rem",
          } as CSSProperties
        }
      >
        <div className="flex h-dvh w-full overflow-hidden bg-background">
          <LayoutSidebar />
          <SidebarInset className="min-w-0 border-0 bg-background">
            <div className="flex h-full min-h-0 flex-col overflow-hidden">
              <LayoutHeader />

              {isMobile ? (
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                  <div className="min-h-0 flex-1">
                    <LayoutRouteOutlet />
                  </div>
                  <MobileTabBar />
                </div>
              ) : (
                <ResizablePanelGroup
                  groupRef={panelGroupRef}
                  orientation="horizontal"
                  defaultLayout={isCanvasOpen ? OPEN_LAYOUT : CLOSED_LAYOUT}
                  className="min-h-0 flex-1"
                >
                  <ResizablePanel id="content" minSize="40%" style={panelStyle}>
                    <LayoutRouteOutlet />
                  </ResizablePanel>

                  <ResizableHandle
                    className={cn(
                      "relative transition-colors",
                      isCanvasOpen ? "w-px bg-border hover:bg-accent" : "pointer-events-none w-0",
                    )}
                    onPointerDown={() => setIsResizing(true)}
                    disabled={!isCanvasOpen}
                  />

                  <ResizablePanel
                    id="canvas"
                    collapsible
                    collapsedSize="0%"
                    minSize="26%"
                    style={panelStyle}
                    onResize={({ asPercentage }) => setIsCanvasOpen(asPercentage > 0)}
                  >
                    <div
                      className={cn(
                        "h-full min-h-0 overflow-hidden transition-opacity duration-200",
                        isCanvasOpen ? "opacity-100" : "opacity-0",
                      )}
                    >
                      <LayoutSidepanel />
                    </div>
                  </ResizablePanel>
                </ResizablePanelGroup>
              )}
            </div>
          </SidebarInset>
        </div>
      </SidebarProvider>

      {isMobile ? (
        <Sheet open={isCanvasOpen} onOpenChange={setIsCanvasOpen}>
          <SheetContent
            side="bottom"
            showCloseButton={false}
            className="h-[min(85dvh,44rem)] gap-0 rounded-t-3xl border-x-0 border-b-0 p-0 sm:max-w-none"
          >
            <SheetHeader className="sr-only">
              <SheetTitle>{panelMeta.title}</SheetTitle>
              <SheetDescription>{panelMeta.description}</SheetDescription>
            </SheetHeader>
            <div className="flex h-full min-h-0 flex-col">
              <div className="flex items-center justify-center py-3">
                <div className="h-1.5 w-10 rounded-full bg-border" aria-hidden="true" />
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                <LayoutSidepanel />
              </div>
            </div>
          </SheetContent>
        </Sheet>
      ) : null}

      <CommandPalette open={cmdOpen} onOpenChange={setCmdOpen} />
      <LoginDialog
        open={loginOpen}
        onOpenChange={setLoginOpen}
        returnFocusRef={loginReturnFocusRef}
      />
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        section={settingsSection}
        onSectionChange={setSettingsSection}
        returnFocusRef={settingsReturnFocusRef}
      />
      <Toaster position={isMobile ? "top-center" : "bottom-right"} />
    </>
  );
}

export function RootLayout() {
  return (
    <AppProviders>
      <AppLayout />
    </AppProviders>
  );
}

export function RootHydrateFallback() {
  return (
    <div className="font-app flex h-dvh w-dvw items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-4">
        <div className="flex w-70 flex-col gap-3">
          <div className="mx-auto h-3 w-20 animate-pulse rounded-md bg-muted" />
          <div className="mx-auto h-2 w-40 animate-pulse rounded-md bg-muted" />
        </div>
      </div>
    </div>
  );
}
