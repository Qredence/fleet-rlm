import { createFileRoute } from "@tanstack/react-router";
import { RootLayout, RootHydrateFallback } from "@/app/layout/RootLayout";
import { RouteErrorPage } from "@/app/pages/RouteErrorPage";

export const Route = createFileRoute("/app")({
  component: RootLayout,
  pendingComponent: RootHydrateFallback,
  errorComponent: RouteErrorPage,
});
