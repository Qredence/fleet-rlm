import { createFileRoute, redirect } from "@tanstack/react-router";
import { AuthView } from "@neondatabase/auth-ui";

import { isNeonAuthConfigured } from "@/lib/auth/neon";
import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/auth/$pathname")({
  beforeLoad: () => {
    if (!isNeonAuthConfigured()) {
      throw redirect({ to: "/app/workspace", replace: true });
    }
  },
  component: AuthScreen,
  errorComponent: RouteErrorScreen,
});

function AuthScreen() {
  const { pathname } = Route.useParams();

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4 py-8">
      <AuthView pathname={pathname} className="w-full max-w-100" />
    </div>
  );
}
