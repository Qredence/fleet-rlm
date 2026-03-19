import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";
import { RouteErrorScreen } from "@/screens/shell/standalone/route-error-screen";

export const Route = createFileRoute("/login")({
  component: lazyRouteComponent(
    () => import("@/screens/shell/standalone/login-screen"),
    "LoginScreen",
  ),
  errorComponent: RouteErrorScreen,
});
