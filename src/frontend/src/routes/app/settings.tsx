import { createFileRoute } from '@tanstack/react-router'
import { LazyRouteComponents } from "@/lib/perf/routePreload"

export const Route = createFileRoute('/app/settings')({
  component: () => <LazyRouteComponents.SettingsPage />,
})
