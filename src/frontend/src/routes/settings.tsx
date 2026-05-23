import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/settings")({
  ssr: false,
  beforeLoad: ({ location }) => {
    throw redirect({
      to: "/app/settings",
      search: location.search,
    });
  },
});
