/**
 * React Query provider for server state management.
 *
 * Configures the QueryClient with defaults tuned for the app:
 *   - 5-minute stale time
 *   - 2 retries with exponential backoff
 *   - Automatic refetch on window focus
 *   - GC time of 10 minutes
 *
 * The QueryClient instance is created once (module-level) and reused
 * across HMR cycles to preserve cache during development.
 */
import { type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const QUERY_STALE_TIME_MS = 5 * 60 * 1000;
const QUERY_RETRY_COUNT = 2;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: QUERY_STALE_TIME_MS,
      gcTime: 10 * 60 * 1000, // 10 minutes
      retry: QUERY_RETRY_COUNT,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
    },
    mutations: {
      retry: 1,
    },
  },
});

interface Props {
  children: ReactNode;
}

function QueryProvider({ children }: Props) {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

export { QueryProvider, queryClient };
