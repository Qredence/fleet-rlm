import { createFileRoute } from '@tanstack/react-router'
import { LazyRouteComponents } from "@/lib/perf/routePreload"
import { RouteErrorPage } from "@/app/pages/RouteErrorPage"

export const Route = createFileRoute('/logout')({
  component: () => <LazyRouteComponents.LogoutPage />,
  errorComponent: RouteErrorPage,
})
