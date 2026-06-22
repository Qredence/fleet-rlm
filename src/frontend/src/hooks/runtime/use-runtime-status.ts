import { useQuery } from "@tanstack/react-query";
import type { QueryFunctionContext } from "@tanstack/react-query";

import { runtimeEndpoints } from "@/lib/rlm-api/runtime";

export const runtimeStatusQueryKey = ["runtime", "status"] as const;

export const runtimeStatusQueryOptions = {
  status: () => ({
    queryKey: runtimeStatusQueryKey,
    queryFn: ({ signal }: QueryFunctionContext) => runtimeEndpoints.status(signal),
    staleTime: 10_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  }),
};

export function useRuntimeStatus(options?: { enabled?: boolean; refetchIntervalMs?: number }) {
  return useQuery({
    ...runtimeStatusQueryOptions.status(),
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
    enabled: typeof window !== "undefined" && (options?.enabled ?? true),
  });
}
