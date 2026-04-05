import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/optimization")({
  component: lazyRouteComponent(
    () => import("@/features/optimization/optimization-screen"),
    "OptimizationScreen",
  ),
});
