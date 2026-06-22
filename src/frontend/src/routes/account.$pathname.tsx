import { createFileRoute, redirect } from "@tanstack/react-router";
import { AccountView } from "@neondatabase/auth-ui";

import { isNeonAuthConfigured } from "@/lib/auth/neon";
import { RouteErrorScreen } from "@/routes/-route-error-screen";

export const Route = createFileRoute("/account/$pathname")({
  beforeLoad: () => {
    if (!isNeonAuthConfigured()) {
      throw redirect({ to: "/app/workspace", replace: true });
    }
  },
  component: AccountScreen,
  errorComponent: RouteErrorScreen,
});

function AccountScreen() {
  const { pathname } = Route.useParams();
  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4 py-8">
      <div className="w-full max-w-3xl">
        <AccountView pathname={pathname} />
      </div>
    </div>
  );
}
