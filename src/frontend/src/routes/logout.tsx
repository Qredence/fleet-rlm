import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";
import { RouteErrorScreen } from "@/screens/shell/standalone/route-error-screen";

export const Route = createFileRoute("/logout")({
  component: lazyRouteComponent(
    () => import("@/screens/shell/standalone/logout-screen"),
    "LogoutScreen",
  ),
  errorComponent: RouteErrorScreen,
});
