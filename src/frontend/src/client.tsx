import { StartClient } from "@tanstack/react-start/client";
import { createRoot, hydrateRoot } from "react-dom/client";
import posthog from "posthog-js";
import { PostHogProvider } from "@posthog/react";

import App from "@/app/App";
import { resolvePostHogWebConfig } from "@/lib/telemetry/posthog";
import "./styles/globals.css";

const posthogConfig = resolvePostHogWebConfig(import.meta.env);

const posthogApiKey = posthogConfig.apiKey;
if (posthogApiKey) {
  const initPostHog = () =>
    posthog.init(posthogApiKey, {
      api_host: posthogConfig.host,
      defaults: "2026-01-30",
    });
  if ("requestIdleCallback" in window) {
    requestIdleCallback(initPostHog, { timeout: 3000 });
  } else {
    setTimeout(initPostHog, 0);
  }
}

const PRELOAD_RELOAD_KEY = "fleetwebapp:vite-preload-retried";

window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();

  const hasRetried = sessionStorage.getItem(PRELOAD_RELOAD_KEY) === "1";
  if (!hasRetried) {
    sessionStorage.setItem(PRELOAD_RELOAD_KEY, "1");
    window.location.reload();
  }
});

window.addEventListener("pageshow", () => {
  sessionStorage.removeItem(PRELOAD_RELOAD_KEY);
});

const hasServerRenderedApp = Array.from(document.body.children).some(
  (child) => child.tagName.toLowerCase() !== "noscript",
);

if (hasServerRenderedApp) {
  hydrateRoot(
    document,
    <PostHogProvider client={posthog}>
      <StartClient />
    </PostHogProvider>,
  );
} else {
  const root = document.createElement("div");
  root.id = "root";
  root.className = "isolate";
  document.body.append(root);
  createRoot(root).render(
    <PostHogProvider client={posthog}>
      <App />
    </PostHogProvider>,
  );
}
