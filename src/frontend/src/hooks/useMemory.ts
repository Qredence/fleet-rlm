/**
 * React Query hooks for Memory entries.
 *
 * Replaces direct `import { mockMemoryEntries } from '@/lib/data/mock-skills'`
 * in consumer components. Returns the stable `MemoryEntry[]` type.
 *
 * In mock mode (no VITE_FLEET_API_URL), returns mock data immediately.
 * In API mode, fetches from `/api/v1/memory` and adapts the response.
 *
 * @example
 * ```tsx
 * const { entries, isLoading, create, update, remove, togglePin } = useMemory();
 * ```
 */
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { isMockMode } from "@/lib/api/config";
import {
  memoryEndpoints,
  type MemoryListParams,
} from "@/lib/api/endpoints";
import { adaptMemoryEntry } from "@/lib/api/adapters";
import {
  getCapabilityStatus,
  type DataSource,
} from "@/lib/api/capabilities";
import { useMockStateStore } from "@/stores/mockStateStore";
import type { MemoryEntry, MemoryType } from "@/lib/data/types";

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
  const mock = isMockMode();
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
        } satisfies MemoryPayload;
      }

      const capability = await getCapabilityStatus("memory", signal);
      if (!capability.available) {
        return {
          entries: filterEntries(memoryEntries),
          dataSource: "fallback" as const,
          degradedReason:
            capability.reason ??
            "Memory endpoint is unavailable, using local mock data.",
        } satisfies MemoryPayload;
      }

      const response = await memoryEndpoints.list(params, signal);
      return {
        entries: (response.items || []).map((item) =>
          adaptMemoryEntry(item as Parameters<typeof adaptMemoryEntry>[0]),
        ),
        dataSource: "api" as const,
      } satisfies MemoryPayload;
    },
    enabled: options?.enabled !== false,
    staleTime: mock ? Infinity : undefined,
  });

  const isMockBacked = mock || query.data?.dataSource === "fallback";

  // ── Create mutation ───────────────────────────────────────────────

  const createMutation = useMutation({
    mutationFn: async (entry: {
      type: MemoryType;
      content: string;
      source?: string;
      tags?: string[];
      pinned?: boolean;
    }) => {
      if (isMockBacked) {
        const now = new Date().toISOString();
        const newEntry: MemoryEntry = {
          id:
            typeof crypto !== "undefined" && "randomUUID" in crypto
              ? `mem-mock-${crypto.randomUUID()}`
              : `mem-mock-${Date.now()}`,
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
      }

      const response = await memoryEndpoints.create({
        type: entry.type,
        content: entry.content,
        source: entry.source,
        tags: entry.tags,
        pinned: entry.pinned,
      });
      return adaptMemoryEntry(
        response as Parameters<typeof adaptMemoryEntry>[0],
      );
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
      if (isMockBacked) {
        updateMemoryEntry(id, patch);
        return memoryEntries.find((e) => e.id === id)!;
      }

      const response = await memoryEndpoints.update(
        id,
        patch as Record<string, unknown>,
      );
      return adaptMemoryEntry(
        response as Parameters<typeof adaptMemoryEntry>[0],
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.all });
    },
  });

  // ── Delete mutation ───────────────────────────────────────────────

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      if (isMockBacked) {
        removeMemoryEntry(id);
        return;
      }

      await memoryEndpoints.delete(id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.all });
    },
  });

  // ── Bulk pin/unpin mutation ───────────────────────────────────────

  const bulkPinMutation = useMutation({
    mutationFn: async ({ ids, pinned }: { ids: string[]; pinned: boolean }) => {
      if (isMockBacked) {
        bulkUpdateMemoryPinned(ids, pinned);
        return memoryEntries.filter((e) => ids.includes(e.id));
      }

      const response = await memoryEndpoints.bulkPin(ids, pinned);
      return response.map((item) =>
        adaptMemoryEntry(item as Parameters<typeof adaptMemoryEntry>[0]),
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: memoryKeys.all });
    },
  });

  // ── Bulk delete mutation ───────────────────────────────────────

  const bulkRemoveMutation = useMutation({
    mutationFn: async (ids: string[]) => {
      if (isMockBacked) {
        bulkRemoveMemoryEntries(ids);
        return;
      }

      await memoryEndpoints.bulkDelete(ids);
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
