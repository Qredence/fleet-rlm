import { useEffect, useRef, useState } from "react";
import type { GroupImperativeHandle } from "react-resizable-panels";

import { AppProviders } from "@/app/providers";
import { CommandPalette } from "@/app/shell/command-palette";
import { LoginDialog } from "@/app/shell/login-dialog";
import { SettingsDialog } from "@/components/settings-dialog";
import { MobileTabBar } from "@/app/shell/mobile-tab-bar";
import { RouteSync } from "@/app/shell/route-sync";
import { ShellRouteOutlet } from "@/app/shell/shell-route-outlet";
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
import { useIsMobile } from "@/hooks/useIsMobile";
import { cn } from "@/lib/utils";
import {
  OPEN_SETTINGS_EVENT,
  type OpenSettingsEventDetail,
} from "@/screens/settings/settings-events";
import type { SettingsSection } from "@/screens/settings/settings-screen";
import { AppSidebar } from "@/screens/shell/app-sidebar";
import { ShellHeader } from "@/screens/shell/shell-header";
import { ShellSidepanel } from "@/screens/shell/shell-sidepanel";
import { useNavigationStore } from "@/stores/navigationStore";

const PANEL_TRANSITION = "flex-grow 350ms cubic-bezier(0.4, 0, 0.2, 1)";
const OPEN_LAYOUT = { content: 64, canvas: 36 };
const CLOSED_LAYOUT = { content: 100, canvas: 0 };

type OpenLoginEventDetail = {
  returnFocusTarget?: HTMLElement | null;
};

function ShellLayout() {
  const isMobile = useIsMobile();
  const [cmdOpen, setCmdOpen] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection | undefined>(undefined);
  const loginReturnFocusRef = useRef<HTMLElement | null>(null);
  const settingsReturnFocusRef = useRef<HTMLElement | null>(null);
  const panelGroupRef = useRef<GroupImperativeHandle>(null);
  const [isResizing, setIsResizing] = useState(false);
  const { isCanvasOpen, setIsCanvasOpen, registerCanvasHandlers } = useNavigationStore();

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
      <SidebarProvider defaultOpen>
        <div className="flex h-dvh w-full overflow-hidden bg-background">
          <AppSidebar />
          <SidebarInset className="min-w-0 border-0 bg-background">
            <div className="flex h-full min-h-0 flex-col overflow-hidden">
              <ShellHeader />

              {isMobile ? (
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                  <div className="min-h-0 flex-1">
                    <ShellRouteOutlet />
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
                    <ShellRouteOutlet />
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
                      <ShellSidepanel />
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
              <SheetTitle>Canvas</SheetTitle>
              <SheetDescription>Workspace detail and artifact view.</SheetDescription>
            </SheetHeader>
            <div className="flex h-full min-h-0 flex-col">
              <div className="flex items-center justify-center py-3">
                <div className="h-1.5 w-10 rounded-full bg-border" aria-hidden="true" />
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                <ShellSidepanel />
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
      <ShellLayout />
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
