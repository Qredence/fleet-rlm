/**
 * React Query hook for the skills list.
 *
 * Replaces direct `import { mockSkills } from '@/lib/data/mock-skills'`
 * in consumer components. Returns the same `Skill[]` type.
 *
 * In mock mode (no VITE_FLEET_API_URL), returns mock data immediately.
 * In API mode, fetches from `/api/v1/tasks` and adapts the response.
 *
 * @example
 * ```tsx
 * const { skills, isLoading, error } = useSkills();
 * const { skills, isLoading } = useSkills({ domain: 'development' });
 * ```
 */
import { useQuery } from "@tanstack/react-query";
import { isMockMode } from "@/lib/api/config";
import { mockSkills, generatedSkillMd } from "@/lib/data/mock-skills";
import { taskEndpoints, type TaskListParams } from "@/lib/api/endpoints";
import { adaptTask } from "@/lib/api/adapters";
import {
  getCapabilityStatus,
  type DataSource,
} from "@/lib/api/capabilities";
import type { Skill } from "@/lib/data/types";

// ── Query Keys ──────────────────────────────────────────────────────

export const skillKeys = {
  all: ["skills"] as const,
  lists: () => [...skillKeys.all, "list"] as const,
  list: (params?: TaskListParams) =>
    [...skillKeys.lists(), params ?? {}] as const,
  details: () => [...skillKeys.all, "detail"] as const,
  detail: (id: string) => [...skillKeys.details(), id] as const,
  content: (id: string) => [...skillKeys.all, "content", id] as const,
};

// ── useSkills ───────────────────────────────────────────────────────

interface UseSkillsOptions {
  /** Filter by domain */
  domain?: string;
  /** Filter by category */
  category?: string;
  /** Filter by status */
  status?: string;
  /** Search query */
  search?: string;
  /** Sort field */
  sortBy?: string;
  /** Sort direction */
  sortOrder?: "asc" | "desc";
  /** Whether to enable the query (default: true) */
  enabled?: boolean;
}

interface UseSkillsReturn {
  /** The skills array — always defined (empty array if loading/error) */
  skills: Skill[];
  /** Data source used to populate results. */
  dataSource: DataSource;
  /** Optional reason when using fallback data instead of API. */
  degradedReason?: string;
  /** True while the initial fetch is in progress */
  isLoading: boolean;
  /** True while a background refetch is in progress */
  isFetching: boolean;
  /** Error object if the fetch failed */
  error: Error | null;
  /** Refetch the skills list */
  refetch: () => void;
}

export function useSkills(options?: UseSkillsOptions): UseSkillsReturn {
  const mock = isMockMode();

  const params: TaskListParams | undefined = options
    ? {
        domain: options.domain,
        category: options.category,
        status: options.status,
        search: options.search,
        sortBy: options.sortBy,
        sortOrder: options.sortOrder,
      }
    : undefined;

  type SkillsPayload = {
    skills: Skill[];
    dataSource: DataSource;
    degradedReason?: string;
  };

  const query = useQuery({
    queryKey: skillKeys.list(params),
    queryFn: async ({ signal }) => {
      if (mock) {
        return {
          skills: mockSkills,
          dataSource: "mock" as const,
        } satisfies SkillsPayload;
      }

      const capability = await getCapabilityStatus("skills", signal);
      if (!capability.available) {
        return {
          skills: mockSkills,
          dataSource: "fallback" as const,
          degradedReason:
            capability.reason ??
            "Skills endpoint is unavailable, using local mock data.",
        } satisfies SkillsPayload;
      }

      const response = await taskEndpoints.list(params, signal);
      return {
        skills: (response.items || []).map((item) =>
          adaptTask(item as Parameters<typeof adaptTask>[0]),
        ),
        dataSource: "api" as const,
      } satisfies SkillsPayload;
    },
    enabled: options?.enabled !== false,
    // In mock mode, data never goes stale
    staleTime: mock ? Infinity : undefined,
  });

  return {
    skills: query.data?.skills ?? [],
    dataSource: query.data?.dataSource ?? (mock ? "mock" : "api"),
    degradedReason: query.data?.degradedReason,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    error: query.error,
    refetch: query.refetch,
  };
}

// ── useSkill (single) ───────────────────────────────────────────────

interface UseSkillReturn {
  skill: Skill | null;
  isLoading: boolean;
  error: Error | null;
}

export function useSkill(id: string | null): UseSkillReturn {
  const mock = isMockMode();

  const query = useQuery({
    queryKey: skillKeys.detail(id ?? ""),
    queryFn: async ({ signal }) => {
      if (!id) return null;
      if (mock) return mockSkills.find((s) => s.id === id) ?? null;

      const capability = await getCapabilityStatus("skills", signal);
      if (!capability.available) {
        return mockSkills.find((s) => s.id === id) ?? null;
      }

      const response = await taskEndpoints.get(id, signal);
      return adaptTask(response as Parameters<typeof adaptTask>[0]);
    },
    enabled: !!id,
    staleTime: mock ? Infinity : undefined,
  });

  return {
    skill: query.data ?? null,
    isLoading: query.isLoading,
    error: query.error,
  };
}

// ── useSkillContent ─────────────────────────────────────────────────

interface UseSkillContentReturn {
  content: string;
  isLoading: boolean;
  error: Error | null;
}

export function useSkillContent(id: string | null): UseSkillContentReturn {
  const mock = isMockMode();

  const query = useQuery({
    queryKey: skillKeys.content(id ?? ""),
    queryFn: async ({ signal }) => {
      if (!id) return "";
      if (mock) {
        return generatedSkillMd;
      }

      const capability = await getCapabilityStatus("skills", signal);
      if (!capability.available) {
        return generatedSkillMd;
      }

      const response = await taskEndpoints.getContent(id, signal);
      return response.content || "";
    },
    enabled: !!id,
    staleTime: mock ? Infinity : undefined,
  });

  return {
    content: query.data ?? "",
    isLoading: query.isLoading,
    error: query.error,
  };
}
