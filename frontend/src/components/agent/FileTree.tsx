import { ChevronRight, Download, FileText, Folder } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { WorkspaceFileEntry } from '@/lib/messages'

type TreeNode =
  | { type: 'folder'; name: string; children: Map<string, TreeNode> }
  | { type: 'file'; name: string; path: string; sizeBytes: number }

function formatFileSize(bytes: number): string {
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

function FileTreeNode({ node, download }: { node: TreeNode; download: (path: string) => void }) {
  if (node.type === 'file') {
    return (
      <Button
        className="group h-8 w-full min-w-0 justify-start gap-2 rounded-xl px-2 text-left font-normal"
        variant="ghost"
        size="sm"
        title={`${node.path} (${formatFileSize(node.sizeBytes)})`}
        aria-label={`Download ${node.path}`}
        onClick={() => download(node.path)}
      >
        <FileText className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-[13px]">{node.name}</span>
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
          {formatFileSize(node.sizeBytes)}
        </span>
        <Download className="size-3.5 shrink-0 text-muted-foreground opacity-60 transition-opacity group-hover:opacity-100" />
      </Button>
    )
  }

  return (
    <Collapsible defaultOpen>
      <CollapsibleTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            className="group h-8 w-full justify-start gap-1.5 rounded-xl px-2 font-medium"
          />
        }
      >
        <ChevronRight className="size-3 shrink-0 text-muted-foreground transition-transform group-aria-expanded:rotate-90" />
        <Folder className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate text-[13px]">{node.name}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="ml-3 border-l pl-1.5">
        {[...node.children].map(([name, child]) => (
          <FileTreeNode key={name} node={child} download={download} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  )
}

export function FileTree({ files, download }: { files: WorkspaceFileEntry[]; download: (path: string) => void }) {
  const roots = new Map<string, TreeNode>()
  for (const { path, size_bytes } of files) {
    const segments = path.split('/')
    const name = segments.pop()!
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
  return (
    <div className="grid min-w-0 gap-0.5">
      {[...roots].map(([name, node]) => <FileTreeNode key={name} node={node} download={download} />)}
    </div>
  )
}
