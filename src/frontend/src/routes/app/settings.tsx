import { createFileRoute, lazyRouteComponent } from "@tanstack/react-router";

export const Route = createFileRoute("/app/settings")({
  component: lazyRouteComponent(
    () => import("@/screens/settings/settings-screen"),
    "SettingsScreen",
  ),
});
