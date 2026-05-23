import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/workspace")({
  ssr: false,
  component: lazyRouteComponent(
    () => import("@/features/workspace/screen/workspace-screen"),
    "WorkspaceScreen",
  ),
});
