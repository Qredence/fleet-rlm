import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";
import { RouteErrorScreen } from "@/screens/shell/standalone/route-error-screen";

export const Route = createFileRoute("/signup")({
  component: lazyRouteComponent(
    () => import("@/screens/shell/standalone/signup-screen"),
    "SignupScreen",
  ),
  errorComponent: RouteErrorScreen,
});
