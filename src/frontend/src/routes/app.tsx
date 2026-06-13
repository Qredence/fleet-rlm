import { createFileRoute } from "@tanstack/react-router";
import { RootLayout, RootHydrateFallback } from "@/features/layout";
import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/app")({
  component: RootLayout,
  pendingComponent: RootHydrateFallback,
  errorComponent: RouteErrorScreen,
});
