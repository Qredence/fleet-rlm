import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/settings")({
  ssr: false,
  component: lazyRouteComponent(
    () => import("@/features/settings/settings-screen"),
    "SettingsScreen",
  ),
});
