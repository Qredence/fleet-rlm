import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/workspace")({
  component: lazyRouteComponent(
    () => import("@/features/workspace/screen/workspace-screen"),
    "WorkspaceScreen",
  ),
});
