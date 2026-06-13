import { createRootRoute, HeadContent, Outlet, Scripts, useRouter } from "@tanstack/react-router";
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools";
import { lazy, Suspense, type ReactNode } from "react";
import { PostHogProvider } from "@posthog/react";
import posthog from "posthog-js";

const Agentation = import.meta.env.DEV
  ? lazy(() => import("agentation").then((m) => ({ default: m.Agentation })))
  : () => null;
const agentationEndpoint = import.meta.env.DEV
  ? (import.meta.env.VITE_AGENTATION_ENDPOINT ?? "http://127.0.0.1:4747")
  : undefined;

function PHProvider({ children }: { children: ReactNode }) {
  return <PostHogProvider client={posthog}>{children}</PostHogProvider>;
}

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
      <PHProvider>
        <Outlet />
      </PHProvider>
      {import.meta.env.DEV && import.meta.env.VITE_E2E !== "1" && <TanStackRouterDevtools />}
      {import.meta.env.DEV && import.meta.env.VITE_E2E !== "1" ? (
        <Suspense fallback={null}>
          <Agentation endpoint={agentationEndpoint} />
        </Suspense>
      ) : null}
    </RootDocument>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const processedManifests = new WeakSet<any>();

function RootDocument({ children }: Readonly<{ children: ReactNode }>) {
  const router = useRouter();
  if (typeof window === "undefined") {
    const manifest = router.ssr?.manifest;
    if (manifest && !processedManifests.has(manifest)) {
      processedManifests.add(manifest);
      if (manifest.routes) {
        for (const routeId of Object.keys(manifest.routes)) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const routeManifest = manifest.routes[routeId] as any;
          if (routeManifest && !routeManifest.assets && routeManifest.scripts) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            routeManifest.assets = routeManifest.scripts.map((script: any) => ({
              tag: "script",
              attrs: script.attrs,
              children: script.children,
            }));
          }
        }
      }
    }
  }

  return (
    <html lang="en" suppressHydrationWarning>
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
