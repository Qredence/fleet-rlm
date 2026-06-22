import { useQuery } from "@tanstack/react-query";
import type { QueryFunctionContext } from "@tanstack/react-query";

import { infoEndpoints } from "@/lib/rlm-api/info";

export const serviceInfoQueryKey = ["service", "info"] as const;

export const serviceInfoQueryOptions = {
  info: () => ({
    queryKey: serviceInfoQueryKey,
    queryFn: ({ signal }: QueryFunctionContext) => infoEndpoints.get(signal),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  }),
};

export function useServiceInfo(options?: { enabled?: boolean }) {
  return useQuery({
    ...serviceInfoQueryOptions.info(),
    enabled: typeof window !== "undefined" && (options?.enabled ?? true),
  });
}
