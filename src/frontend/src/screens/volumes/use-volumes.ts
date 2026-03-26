/**
 * React Query hooks for durable runtime volume filesystem data.
 *
 * Fetches the real volume tree from the backend endpoint
 * GET /api/v1/runtime/volume/tree. Falls back to local mock data
 * or an empty degraded state when the backend is unreachable.
 */
import { useQuery } from "@tanstack/react-query";

import { rlmApiConfig } from "@/lib/rlm-api/config";
import { rlmApiClient, RlmApiError } from "@/lib/rlm-api/client";
import type { DataSource } from "@/lib/rlm-api/capabilities";

export { useVolumesSelectionStore } from "@/screens/volumes/volumes-selection-store";

export type FsNodeType = "volume" | "directory" | "file";
export type VolumeProvider = "modal" | "daytona";

export interface FsNode {
  id: string;
  name: string;
  path: string;
  provider?: VolumeProvider;
  type: FsNodeType;
  children?: FsNode[];
  size?: number;
  mime?: string;
  modifiedAt?: string;
  skillId?: string;
}

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

function createMockFile(
  id: string,
  name: string,
  path: string,
  options?: {
    size?: number;
    mime?: string;
    modifiedAt?: string;
  },
): FsNode {
  return {
    id,
    name,
    path,
    type: "file",
    children: [],
    size: options?.size,
    mime: options?.mime,
    modifiedAt: options?.modifiedAt,
  };
}

function createMockDirectory(
  id: string,
  name: string,
  path: string,
  children: FsNode[],
  modifiedAt?: string,
): FsNode {
  return {
    id,
    name,
    path,
    type: "directory",
    children,
    modifiedAt,
  };
}

function createMockVolume(
  id: string,
  name: string,
  mountedRoot: string,
  children: FsNode[],
  modifiedAt?: string,
): FsNode {
  return {
    id,
    name,
    path: `${mountedRoot}/${name}`,
    type: "volume",
    children,
    modifiedAt,
  };
}

export function getMockFilesystem(provider: VolumeProvider): FsNode[] {
  const mountedRoot = provider === "daytona" ? "/home/daytona/memory" : "/data";
  const modifiedAt = "2026-03-26T08:00:00Z";

  const withProvider = (node: FsNode): FsNode => ({
    ...node,
    provider,
    children: node.children?.map(withProvider),
  });

  return [
    createMockVolume(
      "vol-memory",
      "memory",
      mountedRoot,
      [
        createMockFile(
          "f-memory-facts",
          "facts.json",
          `${mountedRoot}/memory/facts.json`,
          {
            size: 2840,
            mime: "application/json",
            modifiedAt,
          },
        ),
        createMockDirectory(
          "dir-memory-summaries",
          "summaries",
          `${mountedRoot}/memory/summaries`,
          [
            createMockFile(
              "f-memory-summary-md",
              "release-notes.md",
              `${mountedRoot}/memory/summaries/release-notes.md`,
              {
                size: 1820,
                mime: "text/markdown",
                modifiedAt,
              },
            ),
          ],
          modifiedAt,
        ),
      ],
      modifiedAt,
    ),
    createMockVolume(
      "vol-artifacts",
      "artifacts",
      mountedRoot,
      [
        createMockDirectory(
          "dir-artifacts-reports",
          "reports",
          `${mountedRoot}/artifacts/reports`,
          [
            createMockFile(
              "f-artifacts-summary",
              "execution-summary.md",
              `${mountedRoot}/artifacts/reports/execution-summary.md`,
              {
                size: 2140,
                mime: "text/markdown",
                modifiedAt,
              },
            ),
          ],
          modifiedAt,
        ),
        createMockDirectory(
          "dir-artifacts-generated",
          "generated",
          `${mountedRoot}/artifacts/generated`,
          [
            createMockFile(
              "f-artifacts-review",
              "code-review-v2.0.1.tar.gz",
              `${mountedRoot}/artifacts/generated/code-review-v2.0.1.tar.gz`,
              {
                size: 18200,
                mime: "application/gzip",
                modifiedAt,
              },
            ),
          ],
          modifiedAt,
        ),
      ],
      modifiedAt,
    ),
    createMockVolume(
      "vol-buffers",
      "buffers",
      mountedRoot,
      [
        createMockFile(
          "f-buffers-active",
          "active-buffer.txt",
          `${mountedRoot}/buffers/active-buffer.txt`,
          {
            size: 640,
            mime: "text/plain",
            modifiedAt,
          },
        ),
        createMockDirectory(
          "dir-buffers-diffs",
          "diffs",
          `${mountedRoot}/buffers/diffs`,
          [
            createMockFile(
              "f-buffers-patch",
              "current.patch",
              `${mountedRoot}/buffers/diffs/current.patch`,
              {
                size: 980,
                mime: "text/plain",
                modifiedAt,
              },
            ),
          ],
          modifiedAt,
        ),
      ],
      modifiedAt,
    ),
    createMockVolume(
      "vol-meta",
      "meta",
      mountedRoot,
      [
        createMockDirectory(
          "dir-meta-workspaces",
          "workspaces",
          `${mountedRoot}/meta/workspaces`,
          [
            createMockDirectory(
              "dir-meta-workspace-default",
              "default",
              `${mountedRoot}/meta/workspaces/default`,
              [
                createMockFile(
                  "f-meta-manifest",
                  "react-session-default.json",
                  `${mountedRoot}/meta/workspaces/default/react-session-default.json`,
                  {
                    size: 1540,
                    mime: "application/json",
                    modifiedAt,
                  },
                ),
                createMockFile(
                  "f-meta-provenance",
                  "provenance.json",
                  `${mountedRoot}/meta/workspaces/default/provenance.json`,
                  {
                    size: 920,
                    mime: "application/json",
                    modifiedAt,
                  },
                ),
              ],
              modifiedAt,
            ),
          ],
          modifiedAt,
        ),
      ],
      modifiedAt,
    ),
  ].map(withProvider);
}

// ── Query Keys ──────────────────────────────────────────────────────

export const filesystemKeys = {
  all: ["filesystem"] as const,
  tree: (provider: VolumeProvider) =>
    [...filesystemKeys.all, "tree", provider] as const,
  fileContent: (provider: VolumeProvider, path: string) =>
    [...filesystemKeys.all, "file", provider, path] as const,
};

// ── useFilesystem (tree) ────────────────────────────────────────────

interface UseFilesystemReturn {
  /** Durable volume roots — always defined (empty array if loading/error) */
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
          volumes: getMockFilesystem(provider),
          dataSource: "mock",
          degradedReason: undefined,
        };
      }

      try {
        const url = new URL(
          "/api/v1/runtime/volume/tree",
          window.location.origin,
        );
        url.searchParams.set("max_depth", "4");
        url.searchParams.set("provider", provider);
        const resp = await rlmApiClient.get<VolumeTreeResponse>(
          url.pathname + url.search,
          signal,
        );
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
 * Fetches the content of a single file from the durable volume browser.
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

export function formatFileSize(bytes?: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

export function countFiles(node: FsNode): number {
  if (node.type === "file") return 1;
  return (node.children ?? []).reduce((a, c) => a + countFiles(c), 0);
}

export function collectExpandableIds(nodes: FsNode[]): string[] {
  const ids: string[] = [];
  const walk = (list: FsNode[]) => {
    list.forEach((n) => {
      if (n.type !== "file") {
        ids.push(n.id);
        if (n.children) walk(n.children);
      }
    });
  };
  walk(nodes);
  return ids;
}

export function filterFs(nodes: FsNode[], query: string): FsNode[] {
  if (!query) return nodes;
  const q = query.toLowerCase();
  return nodes
    .map((node) => {
      if (node.type === "file") {
        return node.name.toLowerCase().includes(q) ? node : null;
      }
      const filteredChildren = filterFs(node.children ?? [], query);
      if (filteredChildren.length > 0 || node.name.toLowerCase().includes(q)) {
        return { ...node, children: filteredChildren };
      }
      return null;
    })
    .filter(Boolean) as FsNode[];
}
