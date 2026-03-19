import { createFileRoute } from "@tanstack/react-router";
import { RootLayout, RootHydrateFallback } from "@/screens/shell/app-shell-screen";
import { RouteErrorScreen } from "@/screens/shell/standalone/route-error-screen";

export const Route = createFileRoute("/app")({
  component: RootLayout,
  pendingComponent: RootHydrateFallback,
  errorComponent: RouteErrorScreen,
});
