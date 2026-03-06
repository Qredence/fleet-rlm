/**
 * RouteSync — bi-directional sync between the URL and NavigationContext.
 *
 * Rendered inside RootLayout (below both RouterProvider and NavigationProvider).
 * On every location change it updates `activeNav` and clears stale selection
 * state when moving across the supported workspace/volumes surfaces.
 *
 * Direction: URL → NavigationContext (one-way).
 * The reverse direction (NavigationContext → URL) is handled by navigation
 * trigger points using `useAppNavigate()`.
 */
import { useEffect, useRef } from "react";
import { useLocation } from "react-router";
import { pathToNav } from "@/hooks/useAppNavigate";
import { useNavigation } from "@/hooks/useNavigation";

function RouteSync() {
  const location = useLocation();
  const { setActiveNav, selectFile, openCanvas, activeNav } = useNavigation();

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
    } else if (prevSection === "volumes") {
      selectFile(null);
    }
  }, [location.pathname]); // eslint-disable-line react-hooks/exhaustive-deps
  // ↑ Intentionally omit context deps — we only want to run on URL change,
  //   not when context values change (which would create update loops).

  return null;
}

export { RouteSync };
