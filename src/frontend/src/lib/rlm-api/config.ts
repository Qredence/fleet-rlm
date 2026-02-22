/**
 * fleet-rlm core API configuration.
 *
 * Uses Vite `VITE_` env vars and derives sensible defaults for local
 * backend development when values are provided.
 */

function trimOrEmpty(value: string | undefined): string {
  return value?.trim() ?? "";
}

function deriveWsUrl(apiUrl: string): string {
  if (!apiUrl) return "";

  try {
    const url = new URL(apiUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/api/v1/ws/chat";
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return "";
  }
}

function parseBool(value: string | undefined, fallback: boolean): boolean {
  if (value == null) return fallback;
  const normalized = value.trim().toLowerCase();
  if (normalized === "true" || normalized === "1" || normalized === "yes")
    return true;
  if (normalized === "false" || normalized === "0" || normalized === "no")
    return false;
  return fallback;
}

const baseUrl = trimOrEmpty(
  import.meta.env.VITE_FLEET_API_URL as string | undefined,
);
const explicitWsUrl = trimOrEmpty(
  import.meta.env.VITE_FLEET_WS_URL as string | undefined,
);
const mockMode = parseBool(
  import.meta.env.VITE_MOCK_MODE as string | undefined,
  false,
);

function getActiveWsUrl() {
  if (explicitWsUrl) return explicitWsUrl;
  if (baseUrl) return deriveWsUrl(baseUrl);

  // If no baseUrl, derive from current origin if in browser
  if (typeof window !== "undefined") {
    const loc = window.location;
    return `${loc.protocol === "https:" ? "wss:" : "ws:"}//${loc.host}/api/v1/ws/chat`;
  }
  return "";
}

export const rlmApiConfig = {
  baseUrl,
  wsUrl: getActiveWsUrl(),
  mockMode,
  timeoutMs: 30_000,
  workspaceId:
    trimOrEmpty(
      import.meta.env.VITE_FLEET_WORKSPACE_ID as string | undefined,
    ) || "default",
  userId:
    trimOrEmpty(import.meta.env.VITE_FLEET_USER_ID as string | undefined) ||
    "fleetwebapp-user",
  trace: parseBool(
    import.meta.env.VITE_FLEET_TRACE as string | undefined,
    true,
  ),
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
