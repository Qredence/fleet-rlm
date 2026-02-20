/**
 * API Configuration & Environment Detection.
 *
 * Central configuration for the fleet-rlm backend connection.
 * When `VITE_FLEET_API_URL` is not set (e.g. in Figma Make), the entire
 * data layer falls back to local mock data transparently.
 *
 * Environment variables (set in .env or Vite config):
 *   VITE_FLEET_API_URL    — Base URL for fleet-rlm API (e.g. "http://localhost:8000")
 *   VITE_FLEET_WS_URL     — WebSocket URL (e.g. "ws://localhost:8000")
 *   VITE_FLEET_API_KEY    — Optional API key for authenticated requests
 *
 * @example
 * ```ts
 * import { apiConfig, isMockMode } from '../../lib/api/config';
 *
 * if (isMockMode()) {
 *   return mockSkills; // Figma Make or local dev without backend
 * }
 * const url = `${apiConfig.baseUrl}/api/v1/tasks`;
 * ```
 */

// ── Configuration Object ────────────────────────────────────────────

export const apiConfig = {
  /** Base REST API URL. Empty string when not configured. */
  baseUrl: (import.meta.env.VITE_FLEET_API_URL as string | undefined) ?? "",

  /** WebSocket base URL for real-time features. */
  wsUrl: (import.meta.env.VITE_FLEET_WS_URL as string | undefined) ?? "",

  /** Optional API key for authenticated requests. */
  apiKey: (import.meta.env.VITE_FLEET_API_KEY as string | undefined) ?? "",

  /** Request timeout in milliseconds. */
  timeout: 30_000,

  /** Stale time for React Query caches (ms). */
  staleTime: 5 * 60 * 1000, // 5 minutes

  /** Retry count for failed requests. */
  retryCount: 2,
} as const;

// ── Mode Detection ──────────────────────────────────────────────────

/**
 * Returns `true` when no backend URL is configured.
 * All data hooks use this to decide between real API calls and mock data.
 */
export function isMockMode(): boolean {
  return !apiConfig.baseUrl;
}

/**
 * Returns `true` when WebSocket URL is configured.
 */
export function isWsAvailable(): boolean {
  return !!apiConfig.wsUrl;
}
