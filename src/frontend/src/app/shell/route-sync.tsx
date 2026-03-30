/**
 * RouteSync — bi-directional sync between the URL and NavigationStore.
 *
 * Rendered inside RootLayout (below RouterProvider).
 * On every location change it updates `activeNav` and clears stale selection
 * state when moving across the supported workspace/volumes surfaces.
 *
 * Direction: URL → NavigationStore (one-way).
 * The reverse direction (NavigationStore → URL) is handled by navigation
 * trigger points using `useAppNavigate()`.
 */
import { useEffect, useRef } from "react";
import { useRouterState } from "@tanstack/react-router";
import { pathToNav } from "@/hooks/use-app-navigate";
import { useVolumesShellSelection } from "@/screens/volumes/volumes-shell-contract";
import { useNavigationStore } from "@/stores/navigation-store";

function RouteSync() {
  const routerState = useRouterState();
  const location = routerState.location;
  const { setActiveNav, openCanvas, closeCanvas, activeNav } =
    useNavigationStore();
  const { clearSelectedFile } = useVolumesShellSelection();

  // Track previous section to avoid redundant updates
  const prevSectionRef = useRef("");

  useEffect(() => {
    const segments = location.pathname.split("/").filter(Boolean);
    const section = segments[1] ?? "";
    const prevSection = prevSectionRef.current;
    prevSectionRef.current = section;

    // ── Sync activeNav ───────────────────────────────────────────
    const nav = pathToNav(location.pathname);
    if (nav && nav !== activeNav) {
      setActiveNav(nav);
    }

    // ── Sync skill deep-linking ──────────────────────────────────
    if (section === "volumes") {
      openCanvas();
    } else if (section === "settings") {
      closeCanvas();
    } else if (prevSection === "volumes") {
      clearSelectedFile();
    }
  }, [location.pathname]); // oxlint-disable-line react-hooks/exhaustive-deps
  // ↑ Intentionally omit context deps — we only want to run on URL change,
  //   not when context values change (which would create update loops).

  return null;
}

export { RouteSync };
