import { createRootRoute, HeadContent, Outlet, Scripts } from "@tanstack/react-router";
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools";
import { lazy, Suspense, type ReactNode } from "react";

const Agentation = import.meta.env.DEV
  ? lazy(() => import("agentation").then((m) => ({ default: m.Agentation })))
  : () => null;
const agentationEndpoint = import.meta.env.DEV
  ? (import.meta.env.VITE_AGENTATION_ENDPOINT ?? "http://127.0.0.1:4747")
  : undefined;

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1.0" },
      { name: "color-scheme", content: "dark light" },
      {
        name: "theme-color",
        content: "#ffffff",
        media: "(prefers-color-scheme: light)",
      },
      {
        name: "theme-color",
        content: "#212121",
        media: "(prefers-color-scheme: dark)",
      },
      { title: "Qredence" },
    ],
  }),
  component: RootComponent,
});

function RootComponent() {
  return (
    <RootDocument>
      <Outlet />
      {import.meta.env.DEV && import.meta.env.VITE_E2E !== "1" && <TanStackRouterDevtools />}
      {import.meta.env.DEV ? (
        <Suspense fallback={null}>
          <Agentation endpoint={agentationEndpoint} />
        </Suspense>
      ) : null}
    </RootDocument>
  );
}

function RootDocument({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body className="isolate">
        {children}
        <Scripts />
      </body>
    </html>
  );
}
