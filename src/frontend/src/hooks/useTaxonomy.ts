/**
 * React Query hook for the taxonomy tree.
 *
 * Replaces direct `import { mockTaxonomy } from '@/lib/data/mock-skills'`
 * in consumer components. Returns the same `TaxonomyNode[]` type.
 *
 * @example
 * ```tsx
 * const { taxonomy, isLoading } = useTaxonomy();
 * ```
 */
import { useQuery } from "@tanstack/react-query";
import { isMockMode } from "@/lib/api/config";
import { mockTaxonomy } from "@/lib/data/mock-skills";
import { getCapabilityStatus, type DataSource, createFallbackPayload } from "@/lib/api/capabilities";
import type { TaxonomyNode } from "@/lib/data/types";

// ── Query Keys ──────────────────────────────────────────────────────

export const taxonomyKeys = {
  all: ["taxonomy"] as const,
  tree: () => [...taxonomyKeys.all, "tree"] as const,
  subtree: (path: string) => [...taxonomyKeys.all, "subtree", path] as const,
};

// ── useTaxonomy ─────────────────────────────────────────────────────

interface UseTaxonomyReturn {
  /** The full taxonomy tree — always defined (empty array if loading/error) */
  taxonomy: TaxonomyNode[];
  /** Data source used to populate taxonomy. */
  dataSource: DataSource;
  /** Optional reason when local fallback data is used. */
  degradedReason?: string;
  /** True while the initial fetch is in progress */
  isLoading: boolean;
  /** True while a background refetch is in progress */
  isFetching: boolean;
  /** Error object if the fetch failed */
  error: Error | null;
  /** Refetch the taxonomy tree */
  refetch: () => void;
}

export function useTaxonomy(): UseTaxonomyReturn {
  const mock = isMockMode();

  type TaxonomyPayload = {
    taxonomy: TaxonomyNode[];
    dataSource: DataSource;
    degradedReason?: string;
  };

  const query = useQuery({
    queryKey: taxonomyKeys.tree(),
    queryFn: async ({ signal }) => {
      if (mock) {
        return {
          taxonomy: mockTaxonomy,
          dataSource: "mock" as const,
          degradedReason: undefined,
        } satisfies TaxonomyPayload;
      }

      const capability = await getCapabilityStatus("taxonomy", signal);
      if (!capability.available) {
        return createFallbackPayload("taxonomy", mockTaxonomy, capability, "Taxonomy");
      }

      return createFallbackPayload("taxonomy", mockTaxonomy, capability, "Taxonomy");
    },
    staleTime: mock ? Infinity : undefined,
  });

  return {
    taxonomy: query.data?.taxonomy ?? [],
    dataSource: query.data?.dataSource ?? (mock ? "mock" : "api"),
    degradedReason: query.data?.degradedReason,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    error: query.error,
    refetch: query.refetch,
  };
}
