import {
  Asset,
  createRootRoute,
  HeadContent,
  Outlet,
  Scripts,
  useRouter,
  useRouterState,
} from "@tanstack/react-router";
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools";
import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { PostHogProvider } from "@posthog/react";
import posthog from "posthog-js";

const Agentation = import.meta.env.DEV
  ? lazy(() => import("agentation").then((m) => ({ default: m.Agentation })))
  : () => null;
const agentationEndpoint = import.meta.env.DEV
  ? (import.meta.env.VITE_AGENTATION_ENDPOINT ?? "http://127.0.0.1:4747")
  : undefined;

type ManifestScript = {
  attrs?: Record<string, string | boolean | undefined>;
  children?: string;
};

type RouteManifest = {
  assets?: Array<{ tag?: string }>;
  scripts?: ManifestScript[];
};

type SsrManifest = {
  routes?: Record<string, RouteManifest>;
};

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
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      (window as unknown as { __hydrated?: boolean }).__hydrated = true;
    });

    return () => cancelAnimationFrame(frame);
  }, []);

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

function AppScripts() {
  const router = useRouter();
  const matches = useRouterState().matches;
  const nonce = router.options.ssr?.nonce;
  const manifest = router.ssr?.manifest as SsrManifest | undefined;
  const fallbackScripts =
    manifest?.routes == null
      ? []
      : matches.flatMap((match) => {
          const route = router.looseRoutesById[match.routeId];
          const routeManifest = route ? manifest.routes?.[route.id] : undefined;

          if (!routeManifest?.scripts || routeManifest.assets) {
            return [];
          }

          return routeManifest.scripts.map((script) => ({
            tag: "script" as const,
            attrs: { ...script.attrs, nonce },
            children: script.children,
          }));
        });

  return (
    <>
      <Scripts />
      {fallbackScripts.map((script, index) => (
        <Asset key={`fleet-ssr-script-fallback-${index}`} {...script} />
      ))}
    </>
  );
}

function RootDocument({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <HeadContent />
      </head>
      <body className="isolate">
        {children}
        <AppScripts />
      </body>
    </html>
  );
}
