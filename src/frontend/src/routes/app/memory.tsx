import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/app/memory")({
  beforeLoad: () => {
    throw redirect({
      to: "/app/workspace",
    });
  },
});
