/**
 * RouteSync — bi-directional sync between the URL and NavigationContext.
 *
 * Rendered inside RootLayout (below both RouterProvider and NavigationProvider).
 * On every location change it updates `activeNav`, and for skill deep-link URLs
 * (`/skills/:skillId`, `/taxonomy/:skillId`) it also calls `selectSkill` + `openCanvas`.
 *
 * Direction: URL → NavigationContext (one-way).
 * The reverse direction (NavigationContext → URL) is handled by navigation
 * trigger points using `useAppNavigate()`.
 */
import { useEffect, useRef } from "react";
import { useLocation } from "react-router";
import { pathToNav } from "../components/hooks/useAppNavigate";
import { useNavigation } from "../components/hooks/useNavigation";
import { isSectionSupported } from "../lib/rlm-api";

function RouteSync() {
  const location = useLocation();
  const { setActiveNav, selectSkill, selectFile, openCanvas, activeNav } =
    useNavigation();

  // Track previous section to avoid redundant updates
  const prevSectionRef = useRef("");

  useEffect(() => {
    const segments = location.pathname.split("/").filter(Boolean);
    const section = segments[0] ?? "";
    const skillId = segments[1] ?? null;
    const prevSection = prevSectionRef.current;
    prevSectionRef.current = section;

    // ── Sync activeNav ───────────────────────────────────────────
    const nav = pathToNav(location.pathname);
    if (nav && nav !== activeNav) {
      setActiveNav(nav);
    }

    // ── Sync skill deep-linking ──────────────────────────────────
    const isUnsupportedDataSection =
      section === "skills" ||
      section === "taxonomy" ||
      section === "memory" ||
      section === "analytics";
    if (isUnsupportedDataSection) {
      selectSkill(null);
      selectFile(null);
      return;
    }

    if ((section === "skills" || section === "taxonomy") && skillId) {
      if (isSectionSupported(section)) {
        selectSkill(skillId);
        openCanvas();
      }
    } else if (
      (section === "skills" || section === "taxonomy") &&
      !skillId &&
      (prevSection === "skills" || prevSection === "taxonomy")
    ) {
      selectSkill(null);
      selectFile(null);
    }
  }, [location.pathname]); // eslint-disable-line react-hooks/exhaustive-deps
  // ↑ Intentionally omit context deps — we only want to run on URL change,
  //   not when context values change (which would create update loops).

  return null;
}

export { RouteSync };
