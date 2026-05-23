import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/volumes")({
  ssr: false,
  component: lazyRouteComponent(() => import("@/features/volumes/volumes-screen"), "VolumesScreen"),
});
