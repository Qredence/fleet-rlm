import { apiConfig, isMockMode } from "./config";

export type ApiCapabilityKey =
  | "skills"
  | "memory"
  | "taxonomy"
  | "analytics"
  | "filesystem";

export type DataSource = "api" | "mock" | "fallback";

export interface ApiCapabilityStatus {
  available: boolean;
  path: string;
  status?: number;
  reason?: string;
}

export type ApiCapabilities = Record<ApiCapabilityKey, ApiCapabilityStatus>;

const API_PREFIX = "/api/v1";

const CAPABILITY_PATHS: Record<ApiCapabilityKey, string> = {
  skills: `${API_PREFIX}/tasks`,
  memory: `${API_PREFIX}/memory`,
  taxonomy: `${API_PREFIX}/taxonomy`,
  analytics: `${API_PREFIX}/analytics`,
  filesystem: `${API_PREFIX}/sandbox`,
};

const DEFAULT_TIMEOUT_MS = 2500;
const MIN_CACHE_TTL_MS = 15_000;

let cachedCapabilities: ApiCapabilities | null = null;
let cacheExpiryMs = 0;
let inFlightCapabilities: Promise<ApiCapabilities> | null = null;

function buildCapabilityUrl(path: string): string {
  return new URL(path, apiConfig.baseUrl).toString();
}

function isSupportedStatus(status: number): boolean {
  // 404 is definitive unsupported path. Other statuses (401/403/405/5xx)
  // still prove the route exists and should not trigger mock fallback.
  return status !== 404;
}

async function probePath(
  path: string,
  signal?: AbortSignal,
): Promise<ApiCapabilityStatus> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  const combined = signal
    ? anySignal([signal, controller.signal])
    : controller.signal;

  try {
    const response = await fetch(buildCapabilityUrl(path), {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: combined,
    });

    return {
      available: isSupportedStatus(response.status),
      path,
      status: response.status,
      reason: isSupportedStatus(response.status)
        ? undefined
        : `Endpoint ${path} responded with 404`,
    };
  } catch (error) {
    const detail =
      error instanceof Error ? error.message : "Unknown network error";
    return {
      available: false,
      path,
      reason: `Unable to reach ${path}: ${detail}`,
    };
  } finally {
    clearTimeout(timeout);
  }
}

function anySignal(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();

  for (const source of signals) {
    if (source.aborted) {
      controller.abort(source.reason);
      return controller.signal;
    }

    source.addEventListener("abort", () => controller.abort(source.reason), {
      once: true,
    });
  }

  return controller.signal;
}

function allAvailableCapabilities(
  reason = "Mock mode active",
): ApiCapabilities {
  return {
    skills: { available: true, path: CAPABILITY_PATHS.skills, reason },
    memory: { available: true, path: CAPABILITY_PATHS.memory, reason },
    taxonomy: { available: true, path: CAPABILITY_PATHS.taxonomy, reason },
    analytics: { available: true, path: CAPABILITY_PATHS.analytics, reason },
    filesystem: { available: true, path: CAPABILITY_PATHS.filesystem, reason },
  };
}

function allUnavailableCapabilities(reason: string): ApiCapabilities {
  return {
    skills: { available: false, path: CAPABILITY_PATHS.skills, reason },
    memory: { available: false, path: CAPABILITY_PATHS.memory, reason },
    taxonomy: { available: false, path: CAPABILITY_PATHS.taxonomy, reason },
    analytics: { available: false, path: CAPABILITY_PATHS.analytics, reason },
    filesystem: { available: false, path: CAPABILITY_PATHS.filesystem, reason },
  };
}

function buildFallbackReason(
  feature: ApiCapabilityKey,
  status: ApiCapabilityStatus,
): string {
  if (status.reason) {
    return `${feature} data is using local mock fallback because ${status.reason}.`;
  }

  return `${feature} data is using local mock fallback because ${status.path} is unavailable.`;
}

export function resetApiCapabilitiesCache(): void {
  cachedCapabilities = null;
  cacheExpiryMs = 0;
  inFlightCapabilities = null;
}

export async function getApiCapabilities(options?: {
  forceRefresh?: boolean;
  signal?: AbortSignal;
  ttlMs?: number;
}): Promise<ApiCapabilities> {
  if (isMockMode()) {
    const mockCaps = allAvailableCapabilities();
    cachedCapabilities = mockCaps;
    cacheExpiryMs = Date.now() + MIN_CACHE_TTL_MS;
    return mockCaps;
  }

  if (!apiConfig.enableLegacyApiProbes) {
    const disabledCaps = allUnavailableCapabilities(
      "Legacy API probing is disabled by default. Set VITE_FLEET_ENABLE_LEGACY_API_PROBES=true to enable /api/v1 capability probes.",
    );
    cachedCapabilities = disabledCaps;
    cacheExpiryMs = Date.now() + MIN_CACHE_TTL_MS;
    return disabledCaps;
  }

  const now = Date.now();
  const forceRefresh = options?.forceRefresh === true;
  const ttlMs = Math.max(options?.ttlMs ?? 60_000, MIN_CACHE_TTL_MS);

  if (!forceRefresh && cachedCapabilities && now < cacheExpiryMs) {
    return cachedCapabilities;
  }

  if (!forceRefresh && inFlightCapabilities) {
    return inFlightCapabilities;
  }

  inFlightCapabilities = (async () => {
    const entries = await Promise.all(
      (
        Object.entries(CAPABILITY_PATHS) as Array<[ApiCapabilityKey, string]>
      ).map(async ([feature, path]) => {
        const status = await probePath(path, options?.signal);
        return [feature, status] as const;
      }),
    );

    const next = Object.fromEntries(entries) as ApiCapabilities;
    cachedCapabilities = next;
    cacheExpiryMs = Date.now() + ttlMs;
    inFlightCapabilities = null;

    return next;
  })();

  return inFlightCapabilities;
}

export async function getCapabilityStatus(
  feature: ApiCapabilityKey,
  signal?: AbortSignal,
): Promise<ApiCapabilityStatus> {
  const capabilities = await getApiCapabilities({ signal });
  return capabilities[feature];
}

export function dataSourceForCapability(
  mockMode: boolean,
  status: ApiCapabilityStatus,
): { dataSource: DataSource; degradedReason?: string } {
  if (mockMode) {
    return { dataSource: "mock" };
  }

  if (status.available) {
    return { dataSource: "api" };
  }

  return {
    dataSource: "fallback",
    degradedReason: buildFallbackReason(
      (Object.keys(CAPABILITY_PATHS) as ApiCapabilityKey[]).find(
        (k) => CAPABILITY_PATHS[k] === status.path,
      ) ?? "skills",
      status,
    ),
  };
}

export function getCapabilityPath(feature: ApiCapabilityKey): string {
  return CAPABILITY_PATHS[feature];
}
