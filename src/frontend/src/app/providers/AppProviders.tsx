/**
 * Top-level provider composition.
 *
 * Wraps the app in AuthProvider (mock auth) and NavigationProvider (state)
 * so that both DesktopShell and MobileShell can consume shared context.
 */
import type { ReactNode } from "react";
import { AuthProvider } from "../components/hooks/useAuth";
import { NavigationProvider } from "../components/hooks/useNavigation";
import { QueryProvider } from "./QueryProvider";

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
