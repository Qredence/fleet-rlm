/**
 * RootLayout — top-level route component for the authenticated app shell.
 *
 * Renders:
 *   1. AppProviders (AuthProvider + NavigationProvider)
 *   2. RouteSync (URL -> NavigationContext synchronisation)
 *   3. Desktop or Mobile shell (based on viewport)
 *   4. Global overlays (CommandPalette, Toaster)
 *
 * This component is the `Component` for the `/` route in the router config.
 * All child routes render inside `<Outlet />` which lives in ChatPanel.
 *
 * Also exports `RootHydrateFallback` — the skeleton shown by React Router
 * during initial hydration while lazy route modules are being fetched.
 */
import { useState } from "react";
import { AppProviders } from "@/app/providers/AppProviders";
import { DesktopShell } from "@/app/layout/DesktopShell";
import { MobileShell } from "@/app/layout/MobileShell";
import { RouteSync } from "@/app/layout/RouteSync";
import { CommandPalette } from "@/features/shell/CommandPalette";
import { useIsMobile } from "@/components/ui/use-mobile";
import { Toaster } from "@/components/ui/sonner";

/**
 * Inner shell selector — must be inside AppProviders so it can use hooks
 * that depend on context (useIsMobile, etc.).
 */
function ShellSelector() {
  const isMobile = useIsMobile();
  const [cmdOpen, setCmdOpen] = useState(false);

  return (
    <>
      <RouteSync />
      {isMobile ? <MobileShell /> : <DesktopShell />}
      <CommandPalette open={cmdOpen} onOpenChange={setCmdOpen} />
      <Toaster position={isMobile ? "top-center" : "bottom-right"} />
    </>
  );
}

export function RootLayout() {
  return (
    <AppProviders>
      <ShellSelector />
    </AppProviders>
  );
}

/**
 * HydrateFallback — rendered by React Router during initial hydration
 * while the matched lazy route module is being fetched.
 *
 * Styled with design-system CSS variables directly (no context deps)
 * so it works before providers mount.
 */
export function RootHydrateFallback() {
  return (
    <div
      className="flex items-center justify-center h-dvh w-dvw bg-background"
      style={{ fontFamily: "var(--font-family)" }}
    >
      <div className="flex flex-col items-center gap-4">
        {/* Pulsing skeleton blocks that match the app's visual language */}
        <div className="flex w-70 flex-col gap-3">
          <div className="h-3 w-20 rounded-md bg-muted animate-pulse mx-auto" />
          <div className="h-2 w-40 rounded-md bg-muted animate-pulse mx-auto" />
        </div>
      </div>
    </div>
  );
}
