import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/workspace")({
  component: lazyRouteComponent(
    () => import("@/screens/workspace/workspace-screen"),
    "WorkspaceScreen",
  ),
});
