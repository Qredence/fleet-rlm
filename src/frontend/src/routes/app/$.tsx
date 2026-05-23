import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/app/$")({
  ssr: false,
  beforeLoad: () => {
    throw redirect({ to: "/404" });
  },
});
