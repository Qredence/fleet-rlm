import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/volumes")({
  component: lazyRouteComponent(() => import("@/features/volumes/volumes-screen"), "VolumesScreen"),
});
