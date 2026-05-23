import { useQuery } from "@tanstack/react-query";

import { infoEndpoints } from "@/lib/rlm-api/info";

export const serviceInfoQueryKey = ["service", "info"] as const;

export function useServiceInfo(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: serviceInfoQueryKey,
    queryFn: ({ signal }) => infoEndpoints.get(signal),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled: typeof window !== "undefined" && (options?.enabled ?? true),
  });
}
