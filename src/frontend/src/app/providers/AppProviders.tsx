/**
 * Top-level provider composition.
 *
 * Wraps the app in AuthProvider (mock auth) and NavigationProvider (state)
 * so that both DesktopShell and MobileShell can consume shared context.
 */
import type { ReactNode } from "react";
import { AuthProvider } from "@/hooks/useAuth";
import { NavigationProvider } from "@/hooks/useNavigation";
import { QueryProvider } from "@/app/providers/QueryProvider";

interface Props {
  children: ReactNode;
}

function AppProviders({ children }: Props) {
  return (
    <QueryProvider>
      <AuthProvider>
        <NavigationProvider>{children}</NavigationProvider>
      </AuthProvider>
    </QueryProvider>
  );
}

export { AppProviders };
