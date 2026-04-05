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
import { useVolumesLayoutSelection } from "@/screens/volumes/volumes-layout-contract";
import { useNavigationStore } from "@/stores/navigation-store";

function RouteSync() {
  const routerState = useRouterState();
  const location = routerState.location;
  const { setActiveNav, openCanvas, closeCanvas, activeNav } = useNavigationStore();
  const { clearSelectedFile } = useVolumesLayoutSelection();

  const prevSectionRef = useRef("");

  useEffect(() => {
    const segments = location.pathname.split("/").filter(Boolean);
    const section = segments[1] ?? "";
    const prevSection = prevSectionRef.current;
    prevSectionRef.current = section;

    const nav = pathToNav(location.pathname);
    if (nav && nav !== activeNav) {
      setActiveNav(nav);
    }

    if (section === "volumes") {
      openCanvas();
    } else if (section === "settings" || section === "optimization") {
      closeCanvas();
    } else if (prevSection === "volumes") {
      clearSelectedFile();
    }
  }, [location.pathname]); // oxlint-disable-line react-hooks/exhaustive-deps

  return null;
}

export { RouteSync };
