import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

import { queryClient } from "@/lib/query-client";
import { filesystemQueryOptions } from "@/features/volumes";

export const Route = createFileRoute("/app/volumes")({
  component: lazyRouteComponent(() => import("@/features/volumes"), "VolumesScreen"),
  loader: async () => {
    await Promise.allSettled([
      queryClient.ensureQueryData(filesystemQueryOptions.tree("daytona")),
    ]);
  },
});
