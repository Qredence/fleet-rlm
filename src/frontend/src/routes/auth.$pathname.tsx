import { createFileRoute } from "@tanstack/react-router";
import { AuthView } from "@neondatabase/auth-ui";

import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/auth/$pathname")({
  component: AuthScreen,
  errorComponent: RouteErrorScreen,
});

function AuthScreen() {
  const { pathname } = Route.useParams();

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4 py-8">
      <AuthView
        pathname={pathname}
        className="w-full max-w-100"
      />
    </div>
  );
}
