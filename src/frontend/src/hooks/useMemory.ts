/**
 * React Query hooks for Memory entries.
 *
 * Replaces direct `import { mockMemoryEntries } from '@/lib/data/mock-skills'`
 * in consumer components. Returns the stable `MemoryEntry[]` type.
 *
 * In mock mode (no VITE_FLEET_API_URL), returns mock data immediately.
 * In non-mock mode, falls back to local mock data because deprecated memory
 * REST endpoints were removed from the backend.
 *
 * @example
 * ```tsx
 * const { entries, isLoading, create, update, remove, togglePin } = useMemory();
 * ```
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import { rlmApiClient } from "@/lib/rlm-api/client";
import { adaptMemoryEntries } from "@/lib/rlm-api/adapters";
import type { MemoryListParams, DataSource } from "@/lib/rlm-api/capabilities";
import {
  getCapabilityStatus,
  createFallbackPayload,
} from "@/lib/rlm-api/capabilities";
import type { ApiMemoryListResponse } from "@/lib/rlm-api/types";
import { useMockStateStore } from "@/stores/mockStateStore";
import type { MemoryEntry, MemoryType } from "@/lib/data/types";
import { createLocalId } from "@/lib/id";

// ── Query Keys ──────────────────────────────────────────────────────

export const memoryKeys = {
  all: ["memory"] as const,
  lists: () => [...memoryKeys.all, "list"] as const,
  list: (params?: MemoryListParams) =>
    [...memoryKeys.lists(), params ?? {}] as const,
  detail: (id: string) => [...memoryKeys.all, "detail", id] as const,
};

// ── useMemory (list) ────────────────────────────────────────────────

interface UseMemoryOptions {
  /** Filter by memory type */
  type?: MemoryType;
  /** Search query */
  search?: string;
  /** Only pinned entries */
  pinned?: boolean;
  /** Whether to enable the query (default: true) */
  enabled?: boolean;
}

interface UseMemoryReturn {
  /** The memory entries — always defined (empty array if loading/error) */
  entries: MemoryEntry[];
  /** Data source used to populate list results. */
  dataSource: DataSource;
  /** Optional reason when local fallback data is used. */
  degradedReason?: string;
  /** True while the initial fetch is in progress */
  isLoading: boolean;
  /** True while a background refetch is in progress */
  isFetching: boolean;
  /** Error object if the fetch failed */
  error: Error | null;
  /** Refetch the list */
  refetch: () => void;
  /** Create a new memory entry */
  create: (entry: {
    type: MemoryType;
    content: string;
    source?: string;
    tags?: string[];
    pinned?: boolean;
  }) => void;
  /** Update an existing entry */
  update: (
    id: string,
    patch: Partial<
      Pick<MemoryEntry, "content" | "tags" | "pinned" | "relevance">
    >,
  ) => void;
  /** Delete an entry */
  remove: (id: string) => void;
  /** Convenience: toggle pin state */
  togglePin: (id: string) => void;
  /** Bulk pin/unpin a set of entries */
  bulkPin: (ids: string[], pinned: boolean) => void;
  /** Bulk delete a set of entries */
  bulkRemove: (ids: string[]) => void;
  /** Whether a mutation is in progress */
  isMutating: boolean;
}

export function useMemory(options?: UseMemoryOptions): UseMemoryReturn {
  const mock = rlmApiConfig.mockMode;
  const queryClient = useQueryClient();
  const {
    memoryEntries,
    addMemoryEntry,
    updateMemoryEntry,
    removeMemoryEntry,
    bulkUpdateMemoryPinned,
    bulkRemoveMemoryEntries,
  } = useMockStateStore();

  const params: MemoryListParams | undefined = options
    ? {
        type: options.type,
        search: options.search,
        pinned: options.pinned,
      }
    : undefined;

  // ── Query ─────────────────────────────────────────────────────────

  type MemoryPayload = {
    entries: MemoryEntry[];
    dataSource: DataSource;
    degradedReason?: string;
  };

  const query = useQuery({
    queryKey: memoryKeys.list(params),
    queryFn: async ({ signal }) => {
      // Helper to filter entries based on options
      const filterEntries = (entries: MemoryEntry[]): MemoryEntry[] => {
        let filtered = entries;
        if (options?.type)
          filtered = filtered.filter((e) => e.type === options.type);
        if (options?.pinned !== undefined)
          filtered = filtered.filter((e) => e.pinned === options.pinned);
        if (options?.search) {
          const q = options.search.toLowerCase();
          filtered = filtered.filter(
            (e) =>
              e.content.toLowerCase().includes(q) ||
              e.source.toLowerCase().includes(q) ||
              e.tags.some((t) => t.includes(q)),
          );
        }
        return filtered;
      };

      if (mock) {
        return {
          entries: filterEntries(memoryEntries),
          dataSource: "mock" as const,
          degradedReason: undefined,
        } satisfies MemoryPayload;
      }

      const capability = await getCapabilityStatus("memory", signal);
      if (!capability.available) {
        return createFallbackPayload(
          "entries",
          filterEntries(memoryEntries),
          capability,
          "Memory",
        );
      }

      try {
        const qs = new URLSearchParams();
        if (params?.type) qs.set("type", params.type);
        if (params?.search) qs.set("search", params.search);
        if (typeof params?.pinned === "boolean") {
          qs.set("pinned", String(params.pinned));
        }
        if (params?.sortBy) qs.set("sort_by", params.sortBy);
        if (params?.sortOrder) qs.set("sort_order", params.sortOrder);

        const path = qs.toString() ? `/api/v1/memory?${qs}` : "/api/v1/memory";
        const response = await rlmApiClient.get<ApiMemoryListResponse>(
          path,
          signal,
        );

        return {
          entries: adaptMemoryEntries(response.items),
          dataSource: "api" as const,
          degradedReason: undefined,
        } satisfies MemoryPayload;
      } catch {
        return createFallbackPayload(
          "entries",
          filterEntries(memoryEntries),
          { available: false, reason: "memory endpoint request failed" },
          "Memory",
        );
      }
    },
    enabled: options?.enabled !== false,
    staleTime: mock ? Infinity : undefined,
  });

  // ── Create mutation ───────────────────────────────────────────────

  const createMutation = useMutation({
    mutationFn: async (entry: {
      type: MemoryType;
      content: string;
      source?: string;
      tags?: string[];
      pinned?: boolean;
    }) => {
      const now = new Date().toISOString();
      const newEntry: MemoryEntry = {
        id: createLocalId("mem-mock"),
        type: entry.type,
        content: entry.content,
        source: entry.source || "User: Manual Entry",
        createdAt: now,
        updatedAt: now,
        relevance: 80,
        tags: entry.tags || [],
        pinned: entry.pinned ?? false,
      };
      addMemoryEntry(newEntry);
      return newEntry;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.all });
    },
  });

  // ── Update mutation ───────────────────────────────────────────────

  const updateMutation = useMutation({
    mutationFn: async ({
      id,
      patch,
    }: {
      id: string;
      patch: Partial<
        Pick<MemoryEntry, "content" | "tags" | "pinned" | "relevance">
      >;
    }) => {
      updateMemoryEntry(id, patch);
      return useMockStateStore
        .getState()
        .memoryEntries.find((e) => e.id === id)!;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.all });
    },
  });

  // ── Delete mutation ───────────────────────────────────────────────

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      removeMemoryEntry(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.all });
    },
  });

  // ── Bulk pin/unpin mutation ───────────────────────────────────────

  const bulkPinMutation = useMutation({
    mutationFn: async ({ ids, pinned }: { ids: string[]; pinned: boolean }) => {
      bulkUpdateMemoryPinned(ids, pinned);
      return memoryEntries.filter((e) => ids.includes(e.id));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.all });
    },
  });

  // ── Bulk delete mutation ───────────────────────────────────────

  const bulkRemoveMutation = useMutation({
    mutationFn: async (ids: string[]) => {
      bulkRemoveMemoryEntries(ids);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.all });
    },
  });

  // ── Convenience methods ───────────────────────────────────────────

  const create = (entry: Parameters<typeof createMutation.mutate>[0]) => {
    createMutation.mutate(entry);
  };

  const update = (
    id: string,
    patch: Partial<
      Pick<MemoryEntry, "content" | "tags" | "pinned" | "relevance">
    >,
  ) => {
    updateMutation.mutate({ id, patch });
  };

  const remove = (id: string) => {
    deleteMutation.mutate(id);
  };

  const togglePin = (id: string) => {
    const entry = (query.data?.entries ?? []).find((e) => e.id === id);
    if (entry) {
      update(id, { pinned: !entry.pinned });
    }
  };

  const bulkPin = (ids: string[], pinned: boolean) => {
    bulkPinMutation.mutate({ ids, pinned });
  };

  const bulkRemove = (ids: string[]) => {
    bulkRemoveMutation.mutate(ids);
  };

  return {
    entries: query.data?.entries ?? [],
    dataSource: query.data?.dataSource ?? (mock ? "mock" : "api"),
    degradedReason: query.data?.degradedReason,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    error: query.error,
    refetch: query.refetch,
    create,
    update,
    remove,
    togglePin,
    bulkPin,
    bulkRemove,
    isMutating:
      createMutation.isPending ||
      updateMutation.isPending ||
      deleteMutation.isPending ||
      bulkPinMutation.isPending ||
      bulkRemoveMutation.isPending,
  };
}
