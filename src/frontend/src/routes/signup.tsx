import { createFileRoute } from '@tanstack/react-router'
import { LazyRouteComponents } from "@/lib/perf/routePreload"
import { RouteErrorPage } from "@/app/pages/RouteErrorPage"

export const Route = createFileRoute('/signup')({
  component: () => <LazyRouteComponents.SignupPage />,
  errorComponent: RouteErrorPage,
})
