import { createFileRoute } from "@tanstack/react-router";
import { LazyRouteComponents } from "@/lib/perf/routePreload";

export const Route = createFileRoute("/404")({
  component: () => <LazyRouteComponents.NotFoundPage />,
});
