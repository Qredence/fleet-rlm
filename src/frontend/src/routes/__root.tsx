import { createRootRoute, Outlet } from "@tanstack/react-router";
import { TanStackRouterDevtools } from "@tanstack/router-devtools";

export const Route = createRootRoute({
  component: () => (
    <>
      <Outlet />
      {import.meta.env.DEV && import.meta.env.VITE_E2E !== "1" && <TanStackRouterDevtools />}
    </>
  ),
});
