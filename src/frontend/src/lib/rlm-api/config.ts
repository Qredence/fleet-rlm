/**
 * fleet-rlm core API configuration.
 *
 * Uses Vite `VITE_` env vars and derives sensible defaults for local
 * backend development when values are provided.
 */
import { parseBool, trimOrEmpty } from "@/lib/utils/env";

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

function normalizeExplicitWsUrl(wsUrl: string, path: string): string {
  const normalized = wsUrl.replace(/\/$/, "");
  if (path !== "/api/v1/ws/execution") return normalized;
  const legacyChatPath = /\/api\/v1\/ws\/chat(?=\/?$|[?#])/;
  if (!legacyChatPath.test(normalized)) return normalized;
  if (!/^[a-zA-Z][a-zA-Z\d+.-]*:\/\//.test(normalized)) {
    return normalized.replace(legacyChatPath, "/api/v1/ws/execution");
  }

  try {
    const url = new URL(normalized);
    if (url.pathname.endsWith("/api/v1/ws/chat")) {
      url.pathname = url.pathname.replace(/\/api\/v1\/ws\/chat$/, "/api/v1/ws/execution");
      return url.toString().replace(/\/$/, "");
    }
  } catch {
    return normalized.replace(legacyChatPath, "/api/v1/ws/execution");
  }

  return normalized;
}

const baseUrl = trimOrEmpty(import.meta.env.VITE_FLEET_API_URL);
const explicitWsUrl = trimOrEmpty(import.meta.env.VITE_FLEET_WS_URL);
const mockMode = parseBool(import.meta.env.VITE_MOCK_MODE, false);

function getActiveWsUrl(path: string) {
  if (explicitWsUrl) {
    return normalizeExplicitWsUrl(explicitWsUrl, path);
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
  wsUrl: getActiveWsUrl("/api/v1/ws/execution"),
  wsExecutionUrl: getActiveWsUrl("/api/v1/ws/execution"),
  e2eMode: parseBool(import.meta.env.VITE_E2E, false),
  mockMode,
  timeoutMs: 30_000,
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
