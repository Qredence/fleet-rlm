import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/app/taxonomy/$skillId")({
  beforeLoad: () => {
    throw redirect({
      to: "/app/volumes",
    });
  },
});
