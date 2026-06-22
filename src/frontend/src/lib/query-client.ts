import { QueryClient } from "@tanstack/react-query";

const QUERY_STALE_TIME_MS = 5 * 60 * 1000;
const QUERY_RETRY_COUNT = 2;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      enabled: typeof window !== "undefined",
      staleTime: QUERY_STALE_TIME_MS,
      gcTime: 10 * 60 * 1000,
      retry: QUERY_RETRY_COUNT,
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
    },
    mutations: {
      retry: 1,
    },
  },
});
