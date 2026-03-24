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

export const mockFilesystem: FsNode[] = [
  {
    id: "vol-skills",
    name: "skills",
    path: "/sandbox/skills",
    type: "volume",
    modifiedAt: "2026-02-16T08:00:00Z",
    children: [
      {
        id: "dir-sk001",
        name: "data-analysis",
        path: "/sandbox/skills/data-analysis",
        type: "directory",
        skillId: "sk-001",
        modifiedAt: "2026-02-06T12:00:00Z",
        children: [
          {
            id: "f-sk001-md",
            name: "SKILL.md",
            path: "/sandbox/skills/data-analysis/SKILL.md",
            type: "file",
            size: 4820,
            mime: "text/markdown",
            modifiedAt: "2026-02-06T12:00:00Z",
            skillId: "sk-001",
          },
          {
            id: "f-sk001-yaml",
            name: "manifest.yaml",
            path: "/sandbox/skills/data-analysis/manifest.yaml",
            type: "file",
            size: 1240,
            mime: "text/yaml",
            modifiedAt: "2026-02-06T11:30:00Z",
            skillId: "sk-001",
          },
          {
            id: "f-sk001-py",
            name: "handler.py",
            path: "/sandbox/skills/data-analysis/handler.py",
            type: "file",
            size: 3650,
            mime: "text/x-python",
            modifiedAt: "2026-02-06T11:45:00Z",
            skillId: "sk-001",
          },
        ],
      },
      {
        id: "dir-sk002",
        name: "code-review",
        path: "/sandbox/skills/code-review",
        type: "directory",
        skillId: "sk-002",
        modifiedAt: "2026-02-07T09:00:00Z",
        children: [
          {
            id: "f-sk002-md",
            name: "SKILL.md",
            path: "/sandbox/skills/code-review/SKILL.md",
            type: "file",
            size: 5130,
            mime: "text/markdown",
            modifiedAt: "2026-02-07T09:00:00Z",
            skillId: "sk-002",
          },
          {
            id: "f-sk002-yaml",
            name: "manifest.yaml",
            path: "/sandbox/skills/code-review/manifest.yaml",
            type: "file",
            size: 1580,
            mime: "text/yaml",
            modifiedAt: "2026-02-07T08:45:00Z",
            skillId: "sk-002",
          },
          {
            id: "f-sk002-rules",
            name: ".reviewrc.yaml",
            path: "/sandbox/skills/code-review/.reviewrc.yaml",
            type: "file",
            size: 890,
            mime: "text/yaml",
            modifiedAt: "2026-02-07T08:30:00Z",
            skillId: "sk-002",
          },
        ],
      },
      {
        id: "dir-sk005",
        name: "test-generation",
        path: "/sandbox/skills/test-generation",
        type: "directory",
        skillId: "sk-005",
        modifiedAt: "2026-02-07T14:00:00Z",
        children: [
          {
            id: "f-sk005-md",
            name: "SKILL.md",
            path: "/sandbox/skills/test-generation/SKILL.md",
            type: "file",
            size: 6240,
            mime: "text/markdown",
            modifiedAt: "2026-02-07T14:00:00Z",
            skillId: "sk-005",
          },
          {
            id: "f-sk005-yaml",
            name: "manifest.yaml",
            path: "/sandbox/skills/test-generation/manifest.yaml",
            type: "file",
            size: 1820,
            mime: "text/yaml",
            modifiedAt: "2026-02-07T13:45:00Z",
            skillId: "sk-005",
          },
          {
            id: "f-sk005-py",
            name: "handler.py",
            path: "/sandbox/skills/test-generation/handler.py",
            type: "file",
            size: 4210,
            mime: "text/x-python",
            modifiedAt: "2026-02-07T13:30:00Z",
            skillId: "sk-005",
          },
          {
            id: "f-sk005-test",
            name: "test_handler.py",
            path: "/sandbox/skills/test-generation/test_handler.py",
            type: "file",
            size: 2890,
            mime: "text/x-python",
            modifiedAt: "2026-02-07T13:15:00Z",
            skillId: "sk-005",
          },
        ],
      },
    ],
  },
  {
    id: "vol-config",
    name: "config",
    path: "/sandbox/config",
    type: "volume",
    modifiedAt: "2026-02-14T16:00:00Z",
    children: [
      {
        id: "f-fleet-yaml",
        name: "fleet.yaml",
        path: "/sandbox/config/fleet.yaml",
        type: "file",
        size: 2340,
        mime: "text/yaml",
        modifiedAt: "2026-02-14T16:00:00Z",
      },
      {
        id: "f-taxonomy-json",
        name: "taxonomy.json",
        path: "/sandbox/config/taxonomy.json",
        type: "file",
        size: 8920,
        mime: "application/json",
        modifiedAt: "2026-02-14T15:30:00Z",
      },
      {
        id: "f-auth-yaml",
        name: "auth.yaml",
        path: "/sandbox/config/auth.yaml",
        type: "file",
        size: 640,
        mime: "text/yaml",
        modifiedAt: "2026-02-12T10:00:00Z",
      },
      {
        id: "dir-policies",
        name: "policies",
        path: "/sandbox/config/policies",
        type: "directory",
        modifiedAt: "2026-02-13T14:00:00Z",
        children: [
          {
            id: "f-review-policy",
            name: "review-policy.yaml",
            path: "/sandbox/config/policies/review-policy.yaml",
            type: "file",
            size: 420,
            mime: "text/yaml",
            modifiedAt: "2026-02-13T14:00:00Z",
          },
          {
            id: "f-publish-policy",
            name: "publish-policy.yaml",
            path: "/sandbox/config/policies/publish-policy.yaml",
            type: "file",
            size: 380,
            mime: "text/yaml",
            modifiedAt: "2026-02-13T13:30:00Z",
          },
        ],
      },
    ],
  },
  {
    id: "vol-artifacts",
    name: "artifacts",
    path: "/sandbox/artifacts",
    type: "volume",
    modifiedAt: "2026-02-15T10:30:00Z",
    children: [
      {
        id: "dir-art-sessions",
        name: "sessions",
        path: "/sandbox/artifacts/sessions",
        type: "directory",
        modifiedAt: "2026-02-15T10:30:00Z",
        children: [
          {
            id: "f-session-log",
            name: "session-default.jsonl",
            path: "/sandbox/artifacts/sessions/session-default.jsonl",
            type: "file",
            size: 15600,
            mime: "application/jsonl",
            modifiedAt: "2026-02-15T10:30:00Z",
          },
          {
            id: "f-session-2-log",
            name: "session-2.jsonl",
            path: "/sandbox/artifacts/sessions/session-2.jsonl",
            type: "file",
            size: 8400,
            mime: "application/jsonl",
            modifiedAt: "2026-02-10T11:20:00Z",
          },
        ],
      },
      {
        id: "dir-art-generated",
        name: "generated",
        path: "/sandbox/artifacts/generated",
        type: "directory",
        modifiedAt: "2026-02-15T10:30:00Z",
        children: [
          {
            id: "f-gen-tests",
            name: "test-generation-v1.4.2.tar.gz",
            path: "/sandbox/artifacts/generated/test-generation-v1.4.2.tar.gz",
            type: "file",
            size: 24500,
            mime: "application/gzip",
            modifiedAt: "2026-02-07T14:00:00Z",
          },
          {
            id: "f-gen-review",
            name: "code-review-v2.0.1.tar.gz",
            path: "/sandbox/artifacts/generated/code-review-v2.0.1.tar.gz",
            type: "file",
            size: 18200,
            mime: "application/gzip",
            modifiedAt: "2026-02-07T09:00:00Z",
          },
        ],
      },
    ],
  },
  {
    id: "vol-data",
    name: "data",
    path: "/sandbox/data",
    type: "volume",
    modifiedAt: "2026-02-16T08:00:00Z",
    children: [
      {
        id: "f-embeddings",
        name: "skill-embeddings.bin",
        path: "/sandbox/data/skill-embeddings.bin",
        type: "file",
        size: 524288,
        mime: "application/octet-stream",
        modifiedAt: "2026-02-16T08:00:00Z",
      },
      {
        id: "f-index",
        name: "taxonomy-index.json",
        path: "/sandbox/data/taxonomy-index.json",
        type: "file",
        size: 12400,
        mime: "application/json",
        modifiedAt: "2026-02-14T15:30:00Z",
      },
      {
        id: "dir-cache",
        name: "cache",
        path: "/sandbox/data/cache",
        type: "directory",
        modifiedAt: "2026-02-16T07:00:00Z",
        children: [
          {
            id: "f-cache-lru",
            name: "lru-cache.db",
            path: "/sandbox/data/cache/lru-cache.db",
            type: "file",
            size: 65536,
            mime: "application/octet-stream",
            modifiedAt: "2026-02-16T07:00:00Z",
          },
        ],
      },
    ],
  },
];
