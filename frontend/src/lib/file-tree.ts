import type { WorkspaceFileEntry } from './messages'

export type FileTreeNodeData =
  | { type: 'folder'; name: string; children: Map<string, FileTreeNodeData> }
  | { type: 'file'; name: string; path: string; sizeBytes: number }

export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[unitIndex]}`
}

export function buildFileTree(files: WorkspaceFileEntry[]): Map<string, FileTreeNodeData> {
  const roots = new Map<string, FileTreeNodeData>()
  for (const { path, size_bytes } of files) {
    const segments = path.split('/').filter(Boolean)
    const name = segments.pop()
    if (!name) continue
    let level = roots
    for (const segment of segments) {
      let folder = level.get(segment)
      if (folder?.type !== 'folder') {
        folder = { type: 'folder', name: segment, children: new Map() }
        level.set(segment, folder)
      }
      level = folder.children
    }
    level.set(name, { type: 'file', name, path, sizeBytes: size_bytes })
  }
  return roots
}
