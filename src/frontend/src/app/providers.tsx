import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AuthProvider } from "@/lib/auth/auth-provider";

const QUERY_STALE_TIME_MS = 5 * 60 * 1000;
const QUERY_RETRY_COUNT = 2;

interface Props {
  children: ReactNode;
}

function AppProviders({ children }: Props) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            enabled: typeof window !== "undefined",
            staleTime: QUERY_STALE_TIME_MS,
            gcTime: 10 * 60 * 1000,
            retry: QUERY_RETRY_COUNT,
            // Disabled globally — individual queries that need freshness opt in explicitly.
            refetchOnWindowFocus: false,
            refetchOnReconnect: true,
          },
          mutations: {
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}

export { AppProviders };
