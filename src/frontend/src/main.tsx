import { createRoot } from "react-dom/client";
import App from "./app/App.tsx";
import "./styles/index.css";

// PostHog analytics initialization
import posthog from "posthog-js";
import { PostHogProvider } from "@posthog/react";

posthog.init(import.meta.env.VITE_PUBLIC_POSTHOG_KEY, {
  api_host: import.meta.env.VITE_PUBLIC_POSTHOG_HOST,
  defaults: "2026-01-30",
});

const PRELOAD_RELOAD_KEY = "fleetwebapp:vite-preload-retried";

window.addEventListener("vite:preloadError", (event) => {
  // Prevent Vite's default hard failure and retry one full reload.
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

createRoot(document.getElementById("root")!).render(
  <PostHogProvider client={posthog}>
    <App />
  </PostHogProvider>,
);
