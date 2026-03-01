/**
 * React Query hook for the analytics dashboard.
 *
 * Replaces direct `import { analyticsData } from '@/lib/data/mock-skills'`
 * in the AnalyticsDashboard page. Returns the same data shape.
 *
 * @example
 * ```tsx
 * const { analytics, isLoading } = useAnalytics();
 * // analytics.totalSkills, analytics.executionsByDay, etc.
 * ```
 */
import { useQuery } from "@tanstack/react-query";
import { isMockMode } from "@/lib/api/config";
import { analyticsData as mockAnalyticsData } from "@/lib/data/mock-skills";
import type { AnalyticsData } from "@/lib/api/adapters";
import { getCapabilityStatus, type DataSource, createFallbackPayload } from "@/lib/api/capabilities";

// ── Query Keys ──────────────────────────────────────────────────────

export const analyticsKeys = {
  all: ["analytics"] as const,
  dashboard: () => [...analyticsKeys.all, "dashboard"] as const,
  skill: (id: string) => [...analyticsKeys.all, "skill", id] as const,
};

// ── useAnalytics ────────────────────────────────────────────────────

interface UseAnalyticsReturn {
  /** Analytics data — always defined (null-safe defaults if loading) */
  analytics: AnalyticsData;
  /** Data source used to populate analytics. */
  dataSource: DataSource;
  /** Optional reason when local fallback data is used. */
  degradedReason?: string;
  /** True while the initial fetch is in progress */
  isLoading: boolean;
  /** True while a background refetch is in progress */
  isFetching: boolean;
  /** Error object if the fetch failed */
  error: Error | null;
  /** Refetch analytics */
  refetch: () => void;
}

/** Fallback data so consumers never have to null-check. */
const emptyAnalytics: AnalyticsData = {
  totalSkills: 0,
  activeSkills: 0,
  totalExecutions: 0,
  avgQualityScore: 0,
  weeklyGrowth: 0,
  executionsByDay: [],
  topSkills: [],
  qualityDist: [],
};

export function useAnalytics(): UseAnalyticsReturn {
  const mock = isMockMode();

  type AnalyticsPayload = {
    analytics: AnalyticsData;
    dataSource: DataSource;
    degradedReason?: string;
  };

  const query = useQuery({
    queryKey: analyticsKeys.dashboard(),
    queryFn: async ({ signal }) => {
      if (mock) {
        return {
          analytics: mockAnalyticsData as AnalyticsData,
          dataSource: "mock" as const,
          degradedReason: undefined,
        } satisfies AnalyticsPayload;
      }

      const capability = await getCapabilityStatus("analytics", signal);
      if (!capability.available) {
        return createFallbackPayload("analytics", mockAnalyticsData as AnalyticsData, capability, "Analytics");
      }

      return createFallbackPayload("analytics", mockAnalyticsData as AnalyticsData, capability, "Analytics");
    },
    staleTime: mock ? Infinity : undefined,
  });

  return {
    analytics: query.data?.analytics ?? emptyAnalytics,
    dataSource: query.data?.dataSource ?? (mock ? "mock" : "api"),
    degradedReason: query.data?.degradedReason,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    error: query.error,
    refetch: query.refetch,
  };
}
