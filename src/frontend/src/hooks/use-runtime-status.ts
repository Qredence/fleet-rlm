import { useQuery } from "@tanstack/react-query";

import { runtimeEndpoints } from "@/lib/rlm-api/runtime";

export const runtimeStatusQueryKey = ["runtime", "status"] as const;

export function useRuntimeStatus(options?: { enabled?: boolean; refetchIntervalMs?: number }) {
  return useQuery({
    queryKey: runtimeStatusQueryKey,
    queryFn: ({ signal }) => runtimeEndpoints.status(signal),
    staleTime: 10_000,
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
    enabled: options?.enabled ?? true,
  });
}
