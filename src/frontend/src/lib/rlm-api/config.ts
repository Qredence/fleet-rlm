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
  const defaultWsPath = /\/api\/v1\/ws\/(?:chat|execution(?:\/events)?)(?=\/?$|[?#])/;
  if (!defaultWsPath.test(normalized)) return normalized;
  if (!/^[a-zA-Z][a-zA-Z\d+.-]*:\/\//.test(normalized)) {
    return normalized.replace(defaultWsPath, path);
  }

  try {
    const url = new URL(normalized);
    if (/\/api\/v1\/ws\/(?:chat|execution(?:\/events)?)$/.test(url.pathname)) {
      url.pathname = url.pathname.replace(/\/api\/v1\/ws\/(?:chat|execution(?:\/events)?)$/, path);
      return url.toString().replace(/\/$/, "");
    }
  } catch {
    return normalized.replace(defaultWsPath, path);
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
  wsExecutionUrl: getActiveWsUrl("/api/v1/ws/execution/events"),
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
