/**
 * Application route configuration.
 *
 * Uses React Router v7 data mode with `createBrowserRouter`.
 * Route modules are lazy-loaded with retry-aware wrappers so large
 * sections can be split without exposing blank screens on transient
 * chunk-load failures.
 */
import { createBrowserRouter, redirect, type LoaderFunctionArgs } from "react-router";
import { RootLayout, RootHydrateFallback } from "@/app/layout/RootLayout";
import { RouteErrorPage } from "@/app/pages/RouteErrorPage";
import { LazyRouteComponents } from "@/lib/perf/routePreload";

export const router = createBrowserRouter([
  {
    path: "/",
    loader: () => redirect("/app/workspace"),
  },
  {
    path: "/settings",
    loader: ({ request }: LoaderFunctionArgs) => {
      const url = new URL(request.url);
      return redirect(`/app/settings${url.search}`);
    },
  },
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
    path: "/app",
    Component: RootLayout,
    HydrateFallback: RootHydrateFallback,
    ErrorBoundary: RouteErrorPage,
    children: [
      {
        index: true,
        loader: () => redirect("/app/workspace"),
      },
      {
        path: "workspace",
        Component: LazyRouteComponents.RlmWorkspacePage,
      },
      {
        path: "volumes",
        Component: LazyRouteComponents.VolumesPage,
      },
      {
        path: "taxonomy",
        loader: () => redirect("/app/volumes"),
      },
      {
        path: "taxonomy/:skillId",
        loader: () => redirect("/app/volumes"),
      },
      {
        path: "skills",
        loader: () => redirect("/app/workspace"),
      },
      {
        path: "skills/:skillId",
        loader: () => redirect("/app/workspace"),
      },
      {
        path: "memory",
        loader: () => redirect("/app/workspace"),
      },
      {
        path: "analytics",
        loader: () => redirect("/app/workspace"),
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
