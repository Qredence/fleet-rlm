/* eslint-disable react-refresh/only-export-components */
import { Suspense, type ComponentType, type LazyExoticComponent } from "react";
import type { NavItem } from "../../components/data/types";
import { lazyWithRetry, preloadModule } from "./lazyWithRetry";

type RouteKey =
  | "login"
  | "signup"
  | "logout"
  | "notFound"
  | "skillCreation"
  | "skillLibrary"
  | "taxonomy"
  | "memory"
  | "analytics"
  | "settings";

type RouteLoader = () => Promise<{ default: ComponentType<unknown> }>;

const routeLoaders: Record<RouteKey, RouteLoader> = {
  login: async () => {
    const module = await import("../../pages/LoginPage");
    return { default: module.LoginPage };
  },
  signup: async () => {
    const module = await import("../../pages/SignupPage");
    return { default: module.SignupPage };
  },
  logout: async () => {
    const module = await import("../../pages/LogoutPage");
    return { default: module.LogoutPage };
  },
  notFound: async () => {
    const module = await import("../../pages/NotFoundPage");
    return { default: module.NotFoundPage };
  },
  skillCreation: async () => {
    const module = await import("../../pages/SkillCreationFlow");
    return { default: module.SkillCreationFlow };
  },
  skillLibrary: async () => {
    const module = await import("../../pages/SkillLibrary");
    return { default: module.SkillLibrary };
  },
  taxonomy: async () => {
    const module = await import("../../pages/TaxonomyBrowser");
    return { default: module.TaxonomyBrowser };
  },
  memory: async () => {
    const module = await import("../../pages/MemoryPage");
    return { default: module.MemoryPage };
  },
  analytics: async () => {
    const module = await import("../../pages/AnalyticsDashboard");
    return { default: module.AnalyticsDashboard };
  },
  settings: async () => {
    const module = await import("../../pages/SettingsPage");
    return { default: module.SettingsPage };
  },
};

const navPreloadMap: Partial<Record<NavItem, RouteKey>> = {
  new: "skillCreation",
  skills: "skillLibrary",
  taxonomy: "taxonomy",
  memory: "memory",
  analytics: "analytics",
  settings: "settings",
};

function RouteFallback() {
  return (
    <div
      className="flex min-h-[240px] w-full items-center justify-center"
      style={{ fontFamily: "var(--font-family)" }}
    >
      <div className="flex flex-col items-center gap-3">
        <div className="h-2 w-24 animate-pulse rounded bg-muted" />
        <div className="h-2 w-40 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}

function withSuspense(
  Component: LazyExoticComponent<ComponentType<unknown>>,
): ComponentType {
  function SuspendedRoute() {
    return (
      <Suspense fallback={<RouteFallback />}>
        <Component />
      </Suspense>
    );
  }

  SuspendedRoute.displayName = "SuspendedRoute";
  return SuspendedRoute;
}

function lazyRoute(key: RouteKey): ComponentType {
  return withSuspense(lazyWithRetry(`route:${key}`, routeLoaders[key]));
}

export const LazyRouteComponents = {
  LoginPage: lazyRoute("login"),
  SignupPage: lazyRoute("signup"),
  LogoutPage: lazyRoute("logout"),
  NotFoundPage: lazyRoute("notFound"),
  SkillCreationFlow: lazyRoute("skillCreation"),
  SkillLibrary: lazyRoute("skillLibrary"),
  TaxonomyBrowser: lazyRoute("taxonomy"),
  MemoryPage: lazyRoute("memory"),
  AnalyticsDashboard: lazyRoute("analytics"),
  SettingsPage: lazyRoute("settings"),
} as const;

export function preloadRoute(key: RouteKey): Promise<void> {
  return preloadModule(`route:${key}`, routeLoaders[key]);
}

export function preloadNavRoute(nav: NavItem): Promise<void> {
  const key = navPreloadMap[nav];
  if (!key) return Promise.resolve();
  return preloadRoute(key);
}
