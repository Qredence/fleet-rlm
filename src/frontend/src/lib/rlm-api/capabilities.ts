import type { NavItem } from "@/lib/data/types";
import { rlmApiConfig } from "@/lib/rlm-api/config";

// ── Nav section support ─────────────────────────────────────────────

export const SUPPORTED_SECTIONS = new Set<NavItem>([
  "workspace",
  "settings",
  "volumes",
]);

export const UNSUPPORTED_SECTION_REASON =
  "This surface is not part of the current RLM Workspace product contract.";

export function isSectionSupported(nav: NavItem): boolean {
  return SUPPORTED_SECTIONS.has(nav);
}

// ── Data capability types ───────────────────────────────────────────

export type ApiCapabilityKey =
  | "skills"
  | "memory"
  | "taxonomy"
  | "analytics"
  | "filesystem";

export type DataSource = "api" | "mock" | "fallback";

export interface ApiCapabilityStatus {
  available: boolean;
  status?: number;
  reason?: string;
}

export type ApiCapabilities = Record<ApiCapabilityKey, ApiCapabilityStatus>;

// ── Internal helpers ────────────────────────────────────────────────

const CAPABILITY_KEYS: ApiCapabilityKey[] = [
  "skills",
  "memory",
  "taxonomy",
  "analytics",
  "filesystem",
];

const REMOVED_REASON =
  "Endpoint family was removed from backend during deprecated/planned cleanup.";

let cachedCapabilities: ApiCapabilities | null = null;

function allAvailableCapabilities(
  reason = "Mock mode active",
): ApiCapabilities {
  return Object.fromEntries(
    CAPABILITY_KEYS.map((key) => [key, { available: true, reason }]),
  ) as ApiCapabilities;
}

function allUnavailableCapabilities(reason = REMOVED_REASON): ApiCapabilities {
  return Object.fromEntries(
    CAPABILITY_KEYS.map((key) => [key, { available: false, reason }]),
  ) as ApiCapabilities;
}

function buildFallbackReason(
  feature: ApiCapabilityKey,
  status: ApiCapabilityStatus,
): string {
  return status.reason
    ? `${feature} data is using local mock fallback because ${status.reason}.`
    : `${feature} data is using local mock fallback because the endpoint is unavailable.`;
}

// ── Public API ──────────────────────────────────────────────────────

export function resetApiCapabilitiesCache(): void {
  cachedCapabilities = null;
}

export async function getApiCapabilities(_options?: {
  forceRefresh?: boolean;
  signal?: AbortSignal;
  ttlMs?: number;
}): Promise<ApiCapabilities> {
  if (rlmApiConfig.mockMode) {
    const mockCaps = allAvailableCapabilities();
    cachedCapabilities = mockCaps;
    return mockCaps;
  }

  if (cachedCapabilities) {
    return cachedCapabilities;
  }

  const unavailable = allUnavailableCapabilities();
  cachedCapabilities = unavailable;
  return unavailable;
}

export async function getCapabilityStatus(
  feature: ApiCapabilityKey,
  signal?: AbortSignal,
): Promise<ApiCapabilityStatus> {
  void signal;
  const capabilities = await getApiCapabilities();
  return capabilities[feature];
}

export function dataSourceForCapability(
  mockMode: boolean,
  status: ApiCapabilityStatus,
  feature: ApiCapabilityKey,
): { dataSource: DataSource; degradedReason?: string } {
  if (mockMode) {
    return { dataSource: "mock" };
  }

  if (status.available) {
    return { dataSource: "api" };
  }

  return {
    dataSource: "fallback",
    degradedReason: buildFallbackReason(feature, status),
  };
}

export function createFallbackPayload<K extends PropertyKey, T>(
  dataKey: K,
  data: T,
  capability: ApiCapabilityStatus,
  featureName: string,
) {
  return {
    [dataKey]: data,
    dataSource: "fallback" as const,
    degradedReason:
      capability.reason ??
      `${featureName} endpoint is unavailable, using local mock data.`,
  } as Record<K, T> & { dataSource: "fallback"; degradedReason: string };
}

// ── Parameter types shared across hooks ────────────────────────────

export interface TaskListParams {
  page?: number;
  pageSize?: number;
  domain?: string;
  category?: string;
  status?: string;
  search?: string;
  sortBy?: string;
  sortOrder?: "asc" | "desc";
}

export interface MemoryListParams {
  type?: string;
  search?: string;
  pinned?: boolean;
  sortBy?: "relevance" | "createdAt" | "updatedAt";
  sortOrder?: "asc" | "desc";
}
