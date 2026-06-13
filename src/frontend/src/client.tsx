import { StartClient } from "@tanstack/react-start/client";
import { hydrateRoot } from "react-dom/client";
import posthog from "posthog-js";
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

hydrateRoot(document, <StartClient />);

// Rely on standard browser rendering queue to guarantee React has finished layout and paint.
requestAnimationFrame(() => {
  requestAnimationFrame(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__hydrated = true;
  });
});
