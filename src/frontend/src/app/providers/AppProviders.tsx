/**
 * Top-level provider composition.
 *
 * Wraps the app in AuthProvider (mock auth).
 * Navigation state is now handled by Zustand stores (no provider needed).
 */
import type { ReactNode } from "react";
import { AuthProvider } from "@/hooks/useAuth";
import { QueryProvider } from "@/app/providers/QueryProvider";

interface Props {
  children: ReactNode;
}

function AppProviders({ children }: Props) {
  return (
    <QueryProvider>
      <AuthProvider>{children}</AuthProvider>
    </QueryProvider>
  );
}

export { AppProviders };
