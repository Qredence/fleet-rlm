import type { FsNode } from "@/lib/data/types";

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
