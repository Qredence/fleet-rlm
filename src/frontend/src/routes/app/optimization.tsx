import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/optimization")({
  ssr: false,
  component: lazyRouteComponent(
    () => import("@/features/optimization/optimization-screen"),
    "OptimizationScreen",
  ),
});
