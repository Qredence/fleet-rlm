/// <reference types="vite-plus/client" />

interface ImportMetaEnv {
  /** Base URL for the fleet-rlm REST API. */
  readonly VITE_FLEET_API_URL?: string;
  /** Explicit WebSocket URL for fleet-rlm (overrides derived URL). */
  readonly VITE_FLEET_WS_URL?: string;
  /** Enable trace-level logging ("true"/"false"). */
  readonly VITE_FLEET_TRACE?: string;
  /** Enable mock mode — bypass backend entirely ("true"/"false"). */
  readonly VITE_MOCK_MODE?: string;
  /** Marks Playwright-driven dev servers so test-only overlays can be disabled. */
  readonly VITE_E2E?: string;
  /** Neon Auth public branch URL. */
  readonly VITE_NEON_AUTH_URL?: string;
  /** Server-side Neon Auth URL exposed by Vite for local development. */
  readonly NEON_AUTH_URL?: string;
  /** Comma-separated Neon Auth social providers enabled in this deployment. */
  readonly VITE_NEON_AUTH_SOCIAL_PROVIDERS?: string;
  /** PostHog analytics write key. */
  readonly VITE_PUBLIC_POSTHOG_API_KEY?: string;
  /** PostHog ingest host URL. */
  readonly VITE_PUBLIC_POSTHOG_HOST?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
