import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

import { queryClient } from "@/lib/query-client";
import { optimizationQueryOptions } from "@/features/optimization";

export const Route = createFileRoute("/app/optimization")({
  component: lazyRouteComponent(() => import("@/features/optimization"), "OptimizationScreen"),
  loader: async () => {
    await Promise.allSettled([
      queryClient.ensureQueryData(optimizationQueryOptions.status()),
      queryClient.ensureQueryData(optimizationQueryOptions.modules()),
      queryClient.ensureQueryData(optimizationQueryOptions.runs()),
    ]);
  },
});
