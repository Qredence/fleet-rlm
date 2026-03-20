/**
 * React Query hooks for runtime volume filesystem data.
 *
 * Fetches the real volume tree from the backend endpoint
 * GET /api/v1/runtime/volume/tree. Falls back to local mock data
 * or an empty degraded state when the backend is unreachable.
 */
import { useQuery } from "@tanstack/react-query";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import { rlmApiClient, RlmApiError } from "@/lib/rlm-api/client";
import type { DataSource } from "@/lib/rlm-api/capabilities";
import { mockFilesystem } from "@/screens/volumes/model/mock-filesystem";
import type { FsNode, VolumeProvider } from "@/screens/volumes/model/volumes-types";

// ── API response types ──────────────────────────────────────────────

interface VolumeTreeNode {
  id: string;
  name: string;
  path: string;
  type: "volume" | "directory" | "file";
  children: VolumeTreeNode[];
  size?: number | null;
  modifiedAt?: string | null;
}

interface VolumeTreeResponse {
  provider: VolumeProvider;
  volumeName: string;
  rootPath: string;
  nodes: VolumeTreeNode[];
  totalFiles: number;
  totalDirs: number;
  truncated: boolean;
}

interface VolumeFileContentResponse {
  provider: VolumeProvider;
  path: string;
  content: string;
  mime: string;
  size: number;
  truncated: boolean;
}

// ── Conversion ──────────────────────────────────────────────────────

function toFsNode(node: VolumeTreeNode, provider: VolumeProvider): FsNode {
  return {
    id: node.id,
    name: node.name,
    path: node.path,
    provider,
    type: node.type,
    children: node.children?.map((child) => toFsNode(child, provider)),
    size: node.size ?? undefined,
    modifiedAt: node.modifiedAt ?? undefined,
  };
}

function mockNodesForProvider(provider: VolumeProvider): FsNode[] {
  const clone = (node: FsNode): FsNode => ({
    ...node,
    provider,
    children: node.children?.map(clone),
  });
  return mockFilesystem.map(clone);
}

// ── Query Keys ──────────────────────────────────────────────────────

export const filesystemKeys = {
  all: ["filesystem"] as const,
  tree: (provider: VolumeProvider) => [...filesystemKeys.all, "tree", provider] as const,
  fileContent: (provider: VolumeProvider, path: string) =>
    [...filesystemKeys.all, "file", provider, path] as const,
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

export function useFilesystem(provider: VolumeProvider): UseFilesystemReturn {
  const mock = rlmApiConfig.mockMode;

  type FilesystemPayload = {
    volumes: FsNode[];
    dataSource: DataSource;
    degradedReason?: string;
  };

  const query = useQuery({
    queryKey: filesystemKeys.tree(provider),
    queryFn: async ({ signal }): Promise<FilesystemPayload> => {
      if (mock) {
        return {
          volumes: mockNodesForProvider(provider),
          dataSource: "mock",
          degradedReason: undefined,
        };
      }

      try {
        const url = new URL("/api/v1/runtime/volume/tree", window.location.origin);
        url.searchParams.set("max_depth", "4");
        url.searchParams.set("provider", provider);
        const resp = await rlmApiClient.get<VolumeTreeResponse>(url.pathname + url.search, signal);
        return {
          volumes: resp.nodes.map((node) => toFsNode(node, resp.provider)),
          dataSource: "api",
        };
      } catch (err) {
        const reason =
          err instanceof RlmApiError
            ? `${provider === "daytona" ? "Daytona" : "Modal"} volume API returned ${err.status}: ${err.detail}`
            : `${provider === "daytona" ? "Daytona" : "Modal"} volume API unreachable.`;
        return {
          volumes: [],
          dataSource: "fallback",
          degradedReason: reason,
        };
      }
    },
    staleTime: mock ? Infinity : 30_000,
    retry: false,
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
export function useFileContent(
  path: string | null,
  provider: VolumeProvider,
): UseFileContentReturn {
  const mock = rlmApiConfig.mockMode;

  const query = useQuery({
    queryKey: filesystemKeys.fileContent(provider, path ?? ""),
    queryFn: async ({ signal }) => {
      if (!path) return { content: "", mime: "", size: 0 };

      const qs = new URLSearchParams({
        path,
        max_bytes: "200000",
        provider,
      }).toString();
      const resp = await rlmApiClient.get<VolumeFileContentResponse>(
        `/api/v1/runtime/volume/file?${qs}`,
        signal,
      );

      return {
        content: resp.content,
        mime: resp.mime,
        size: resp.size,
      };
    },
    enabled: !!path,
    staleTime: mock ? Infinity : undefined,
    retry: false,
  });

  return {
    content: query.data?.content ?? "",
    mime: query.data?.mime ?? "",
    size: query.data?.size ?? 0,
    isLoading: query.isLoading,
    error: query.error,
  };
}
