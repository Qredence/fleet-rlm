import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/volumes")({
  component: lazyRouteComponent(
    () => import("@/screens/volumes/volumes-screen"),
    "VolumesScreen",
  ),
});
