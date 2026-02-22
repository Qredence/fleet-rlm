/**
 * Debounced search hook.
 *
 * Provides a 300ms debounced search across skills (and optionally
 * taxonomy) with fallback to in-memory filtering in mock mode.
 *
 * @example
 * ```tsx
 * const { results, isSearching } = useSearch(searchQuery);
 * ```
 */
import { useQuery } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { isMockMode } from "@/lib/api/config";
import { mockSkills } from "@/lib/data/mock-skills";
import { searchEndpoints } from "@/lib/api/endpoints";
import { adaptTask } from "@/lib/api/adapters";
import type { Skill } from "@/lib/data/types";

// ── Query Keys ──────────────────────────────────────────────────────

export const searchKeys = {
  all: ["search"] as const,
  query: (q: string) => [...searchKeys.all, q] as const,
};

// ── Debounce helper ─────────────────────────────────────────────────

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}

// ── useSearch ───────────────────────────────────────────────────────

interface UseSearchReturn {
  /** Matching skills */
  results: Skill[];
  /** True while searching */
  isSearching: boolean;
  /** The debounced query string that was actually searched */
  debouncedQuery: string;
}

export function useSearch(query: string, debounceMs = 300): UseSearchReturn {
  const mock = isMockMode();
  const debouncedQuery = useDebouncedValue(query.trim(), debounceMs);

  const searchQuery = useQuery({
    queryKey: searchKeys.query(debouncedQuery),
    queryFn: async ({ signal }) => {
      if (!debouncedQuery) return [];

      if (mock) {
        // In-memory search across mock skills
        const lower = debouncedQuery.toLowerCase();
        return mockSkills.filter(
          (s) =>
            s.displayName.toLowerCase().includes(lower) ||
            s.name.toLowerCase().includes(lower) ||
            s.description.toLowerCase().includes(lower) ||
            s.tags.some((t) => t.toLowerCase().includes(lower)) ||
            s.domain.toLowerCase().includes(lower),
        );
      }

      const response = await searchEndpoints.search(debouncedQuery, signal);
      return (response.skills || []).map((item) =>
        adaptTask(item as Parameters<typeof adaptTask>[0]),
      );
    },
    enabled: debouncedQuery.length > 0,
    staleTime: mock ? Infinity : 30_000, // 30s stale for real API searches
  });

  return {
    results: searchQuery.data ?? [],
    isSearching: searchQuery.isFetching,
    debouncedQuery,
  };
}
