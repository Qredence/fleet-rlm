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
import { useNavigate } from "@tanstack/react-router";
import type { NavItem } from "@/lib/data/types";

// ── Nav ↔ Path mapping ─────────────────────────────────────────────

const NAV_TO_PATH: Record<NavItem, string> = {
  workspace: "/app/workspace",
  volumes: "/app/volumes",
  settings: "/app/settings",
};

export function navToPath(nav: NavItem): string {
  return NAV_TO_PATH[nav] ?? "/app";
}

export function pathToNav(pathname: string): NavItem | null {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] !== "app") return null;
  const section = parts[1] ?? "";

  const PATH_TO_NAV: Record<string, NavItem> = {
    "": "workspace",
    workspace: "workspace",
    volumes: "volumes",
    taxonomy: "volumes",
    skills: "workspace",
    memory: "workspace",
    analytics: "workspace",
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
      navigate({ to: navToPath(nav) as any });
    },
    [navigate],
  );

  return { navigate, navigateTo };
}
