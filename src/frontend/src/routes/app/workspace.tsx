import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

import { queryClient } from "@/lib/query-client";
import { runtimeStatusQueryOptions } from "@/hooks/runtime/use-runtime-status";
import { serviceInfoQueryOptions } from "@/hooks/runtime/use-service-info";

export const Route = createFileRoute("/app/workspace")({
  component: lazyRouteComponent(() => import("@/features/workspace"), "WorkspaceScreen"),
  loader: async () => {
    await Promise.allSettled([
      queryClient.ensureQueryData(runtimeStatusQueryOptions.status()),
      queryClient.ensureQueryData(serviceInfoQueryOptions.info()),
    ]);
  },
});
