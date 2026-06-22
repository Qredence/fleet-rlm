import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

import { queryClient } from "@/lib/query-client";
import { runtimeSettingsQueryOptions, llmProfilesQueryOptions } from "@/features/settings";
import { runtimeStatusQueryOptions } from "@/hooks/runtime/use-runtime-status";

export const Route = createFileRoute("/app/settings")({
  component: lazyRouteComponent(() => import("@/features/settings"), "SettingsScreen"),
  loader: async () => {
    await Promise.allSettled([
      queryClient.ensureQueryData(runtimeSettingsQueryOptions.settings()),
      queryClient.ensureQueryData(llmProfilesQueryOptions.profiles()),
      queryClient.ensureQueryData(llmProfilesQueryOptions.roleBindings()),
      queryClient.ensureQueryData(runtimeStatusQueryOptions.status()),
    ]);
  },
});
