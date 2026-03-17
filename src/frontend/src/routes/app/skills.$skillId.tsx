import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/app/skills/$skillId")({
  beforeLoad: () => {
    throw redirect({
      to: "/app/workspace",
    });
  },
});
