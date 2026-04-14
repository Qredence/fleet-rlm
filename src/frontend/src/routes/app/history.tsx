import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/history")({
  component: lazyRouteComponent(
    () => import("@/features/history/history-screen"),
    "HistoryScreen",
  ),
});
