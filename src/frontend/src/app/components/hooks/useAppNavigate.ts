/**
 * Application-level navigation hook.
 *
 * Wraps React Router's `useNavigate()` with convenience methods that
 * know the app's route structure. All navigation trigger points
 * (TopHeader, MobileTabBar, CommandPalette, etc.) should use this hook
 * instead of calling `setActiveNav()` directly.
 *
 * The URL is the source of truth — RouteSync in RootLayout watches URL
 * changes and syncs NavigationContext accordingly.
 */
import { useCallback } from "react";
import { useNavigate } from "react-router";
import type { NavItem } from "../data/types";

// ── Nav ↔ Path mapping ─────────────────────────────────────────────

const NAV_TO_PATH: Record<NavItem, string> = {
  new: "/",
  skills: "/skills",
  taxonomy: "/taxonomy",
  memory: "/memory",
  analytics: "/analytics",
  settings: "/settings",
};

export function navToPath(nav: NavItem): string {
  return NAV_TO_PATH[nav] ?? "/";
}

export function pathToNav(pathname: string): NavItem | null {
  const section = pathname.split("/").filter(Boolean)[0] ?? "";
  const PATH_TO_NAV: Record<string, NavItem> = {
    "": "new",
    skills: "skills",
    taxonomy: "taxonomy",
    memory: "memory",
    analytics: "analytics",
    settings: "settings",
  };
  return PATH_TO_NAV[section] ?? null;
}

// ── Hook ────────────────────────────────────────────────────────────

export function useAppNavigate() {
  const navigate = useNavigate();

  /** Navigate to a top-level tab/section */
  const navigateTo = useCallback(
    (nav: NavItem) => {
      navigate(navToPath(nav));
    },
    [navigate],
  );

  /** Navigate to a specific skill within a section (opens BuilderPanel) */
  const navigateToSkill = useCallback(
    (section: "skills" | "taxonomy", skillId: string) => {
      navigate(`/${section}/${skillId}`);
    },
    [navigate],
  );

  /** Navigate to section root (e.g. deselect skill) */
  const navigateToSection = useCallback(
    (section: "skills" | "taxonomy") => {
      navigate(`/${section}`);
    },
    [navigate],
  );

  return { navigate, navigateTo, navigateToSkill, navigateToSection };
}
