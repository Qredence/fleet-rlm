/**
 * Application route configuration.
 *
 * Uses React Router v7 data mode with `createBrowserRouter`.
 * Route modules are lazy-loaded with retry-aware wrappers so large
 * sections can be split without exposing blank screens on transient
 * chunk-load failures.
 */
import { createBrowserRouter } from "react-router";
import { RootLayout, RootHydrateFallback } from "@/app/layout/RootLayout";
import { RouteErrorPage } from "@/app/pages/RouteErrorPage";
import { LazyRouteComponents } from "@/lib/perf/routePreload";

export const router = createBrowserRouter([
  {
    path: "/login",
    Component: LazyRouteComponents.LoginPage,
    ErrorBoundary: RouteErrorPage,
  },
  {
    path: "/signup",
    Component: LazyRouteComponents.SignupPage,
    ErrorBoundary: RouteErrorPage,
  },
  {
    path: "/logout",
    Component: LazyRouteComponents.LogoutPage,
    ErrorBoundary: RouteErrorPage,
  },
  {
    path: "/404",
    Component: LazyRouteComponents.NotFoundPage,
    ErrorBoundary: RouteErrorPage,
  },
  {
    path: "/",
    Component: RootLayout,
    HydrateFallback: RootHydrateFallback,
    ErrorBoundary: RouteErrorPage,
    children: [
      {
        index: true,
        Component: LazyRouteComponents.SkillCreationFlow,
      },
      {
        path: "skills",
        Component: LazyRouteComponents.UnsupportedSkillsPage,
      },
      {
        path: "skills/:skillId",
        Component: LazyRouteComponents.UnsupportedSkillsPage,
      },
      {
        path: "taxonomy",
        Component: LazyRouteComponents.UnsupportedTaxonomyPage,
      },
      {
        path: "taxonomy/:skillId",
        Component: LazyRouteComponents.UnsupportedTaxonomyPage,
      },
      {
        path: "memory",
        Component: LazyRouteComponents.UnsupportedMemoryPage,
      },
      {
        path: "analytics",
        Component: LazyRouteComponents.UnsupportedAnalyticsPage,
      },
      {
        path: "settings",
        Component: LazyRouteComponents.SettingsPage,
      },
      {
        path: "*",
        Component: LazyRouteComponents.NotFoundPage,
      },
    ],
  },
]);
