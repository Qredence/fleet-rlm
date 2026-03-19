import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/404")({
  component: lazyRouteComponent(
    () => import("@/screens/shell/standalone/not-found-screen"),
    "NotFoundScreen",
  ),
});
