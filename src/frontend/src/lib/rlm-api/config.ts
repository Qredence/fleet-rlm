/**
 * fleet-rlm core API configuration.
 *
 * Uses Vite `VITE_` env vars and derives sensible defaults for local
 * backend development when values are provided.
 */

import { parseBool } from "@/lib/utils/env";

function trimOrEmpty(value: string | undefined): string {
  return value?.trim() ?? "";
}

function deriveWsUrl(apiUrl: string, path: string): string {
  if (!apiUrl) return "";

  try {
    const url = new URL(apiUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = path;
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return "";
  }
}

const baseUrl = trimOrEmpty(import.meta.env.VITE_FLEET_API_URL);
const explicitWsUrl = trimOrEmpty(import.meta.env.VITE_FLEET_WS_URL);
const mockMode = parseBool(import.meta.env.VITE_MOCK_MODE, false);

function getActiveWsUrl(path: string) {
  if (explicitWsUrl) {
    if (path === "/api/v1/ws/execution") {
      if (explicitWsUrl.endsWith("/chat")) {
        return explicitWsUrl.replace(/\/chat$/, "/execution");
      }
      // Explicit URL doesn't end in /chat — derive execution URL from its origin
      try {
        const parsed = new URL(explicitWsUrl);
        parsed.pathname = "/api/v1/ws/execution";
        return parsed.toString().replace(/\/$/, "");
      } catch {
        // Malformed URL; fall through to use explicitWsUrl as-is
      }
    }
    return explicitWsUrl;
  }
  if (baseUrl) return deriveWsUrl(baseUrl, path);

  // If no baseUrl, derive from current origin if in browser
  if (typeof window !== "undefined") {
    const loc = window.location;
    return `${loc.protocol === "https:" ? "wss:" : "ws:"}//${loc.host}${path}`;
  }
  return "";
}

export const rlmApiConfig = {
  baseUrl,
  wsUrl: getActiveWsUrl("/api/v1/ws/chat"),
  wsExecutionUrl: getActiveWsUrl("/api/v1/ws/execution"),
  mockMode,
  timeoutMs: 30_000,
  workspaceId: trimOrEmpty(import.meta.env.VITE_FLEET_WORKSPACE_ID) || "default",
  userId: trimOrEmpty(import.meta.env.VITE_FLEET_USER_ID) || "fleetwebapp-user",
  trace: parseBool(import.meta.env.VITE_FLEET_TRACE, true),
} as const;

/**
 * Core backend mode is enabled when we are not in mock mode.
 */
export function isRlmCoreEnabled(): boolean {
  return !rlmApiConfig.mockMode;
}

export function isRlmWsEnabled(): boolean {
  return !rlmApiConfig.mockMode;
}
