/**
 * React Query hooks for sandbox filesystem data.
 *
 * Replaces direct `import { mockFilesystem } from '@/lib/data/mock-skills'`
 * in consumer components. Returns the stable `FsNode[]` type.
 *
 * In mock mode (no VITE_FLEET_API_URL), returns mock data immediately.
 * In non-mock mode, falls back to local mock data because deprecated sandbox
 * REST endpoints were removed from the backend.
 *
 * @example
 * ```tsx
 * const { volumes, isLoading } = useFilesystem();
 * const { content, isLoading } = useFileContent('/sandbox/config/fleet.yaml');
 * ```
 */
import { useQuery } from "@tanstack/react-query";
import { isMockMode } from "@/lib/api/config";
import { mockFilesystem } from "@/lib/data/mock-skills";
import { getCapabilityStatus, type DataSource, createFallbackPayload } from "@/lib/api/capabilities";
import type { FsNode } from "@/lib/data/types";

// ── Query Keys ──────────────────────────────────────────────────────

export const filesystemKeys = {
  all: ["filesystem"] as const,
  tree: () => [...filesystemKeys.all, "tree"] as const,
  fileContent: (path: string) => [...filesystemKeys.all, "file", path] as const,
};

// ── useFilesystem (tree) ────────────────────────────────────────────

interface UseFilesystemReturn {
  /** Sandbox volumes — always defined (empty array if loading/error) */
  volumes: FsNode[];
  /** Data source used to populate filesystem data. */
  dataSource: DataSource;
  /** Optional reason when local fallback data is used. */
  degradedReason?: string;
  /** True while the initial fetch is in progress */
  isLoading: boolean;
  /** True while a background refetch is in progress */
  isFetching: boolean;
  /** Error object if the fetch failed */
  error: Error | null;
  /** Refetch the filesystem tree */
  refetch: () => void;
}

export function useFilesystem(): UseFilesystemReturn {
  const mock = isMockMode();

  type FilesystemPayload = {
    volumes: FsNode[];
    dataSource: DataSource;
    degradedReason?: string;
  };

  const query = useQuery({
    queryKey: filesystemKeys.tree(),
    queryFn: async ({ signal }) => {
      if (mock) {
        return {
          volumes: mockFilesystem,
          dataSource: "mock" as const,
          degradedReason: undefined,
        } satisfies FilesystemPayload;
      }

      const capability = await getCapabilityStatus("filesystem", signal);
      if (!capability.available) {
        return createFallbackPayload("volumes", mockFilesystem, capability, "Filesystem");
      }

      return createFallbackPayload("volumes", mockFilesystem, capability, "Filesystem");
    },
    staleTime: mock ? Infinity : undefined,
  });

  return {
    volumes: query.data?.volumes ?? [],
    dataSource: query.data?.dataSource ?? (mock ? "mock" : "api"),
    degradedReason: query.data?.degradedReason,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    error: query.error,
    refetch: query.refetch,
  };
}

// ── useFileContent (single file) ────────────────────────────────────

interface UseFileContentReturn {
  /** File content string (empty string while loading or on error) */
  content: string;
  /** MIME type */
  mime: string;
  /** File size in bytes */
  size: number;
  /** True while fetching */
  isLoading: boolean;
  /** Error object */
  error: Error | null;
}

/**
 * Fetches the content of a single file from the sandbox.
 * Only enabled when `path` is non-null.
 *
 * In mock mode, returns a placeholder message — file content is not
 * stored in the mock filesystem tree (only metadata). The FileDetail
 * component has its own MOCK_FILE_CONTENT map for preview purposes.
 */
export function useFileContent(path: string | null): UseFileContentReturn {
  const mock = isMockMode();

  const query = useQuery({
    queryKey: filesystemKeys.fileContent(path ?? ""),
    queryFn: async ({ signal }) => {
      if (!path) return { content: "", mime: "", size: 0 };
      if (mock) {
        return {
          content: "",
          mime: "text/plain",
          size: 0,
        };
      }

      const capability = await getCapabilityStatus("filesystem", signal);
      if (!capability.available) {
        return {
          content: "",
          mime: "text/plain",
          size: 0,
        };
      }

      return {
        content: "",
        mime: "text/plain",
        size: 0,
      };
    },
    enabled: !!path,
    staleTime: mock ? Infinity : undefined,
  });

  return {
    content: query.data?.content ?? "",
    mime: query.data?.mime ?? "",
    size: query.data?.size ?? 0,
    isLoading: query.isLoading,
    error: query.error,
  };
}
