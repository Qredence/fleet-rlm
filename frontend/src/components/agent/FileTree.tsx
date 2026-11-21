import { ChevronRight, Download, FileText, Folder } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { WorkspaceFileEntry } from '@/lib/messages'

type TreeNode =
  | { type: 'folder'; name: string; children: Map<string, TreeNode> }
  | { type: 'file'; name: string; path: string }

function FileTreeNode({ node, download }: { node: TreeNode; download: (path: string) => void }) {
  if (node.type === 'file') {
    return (
      <Button
        className="group w-full min-w-0 justify-start text-left font-normal"
        variant="ghost"
        size="sm"
        title={node.path}
        aria-label={`Download ${node.path}`}
        onClick={() => download(node.path)}
      >
        <FileText className="shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
        <Download className="shrink-0 text-muted-foreground" />
      </Button>
    )
  }

  return (
    <Collapsible defaultOpen>
      <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="group w-full justify-start font-medium" />}>
        <ChevronRight className="size-3 transition-transform group-aria-expanded:rotate-90" />
        <Folder className="text-muted-foreground" />
        <span className="truncate">{node.name}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="ml-3 border-l pl-1">
        {[...node.children].map(([name, child]) => <FileTreeNode key={name} node={child} download={download} />)}
      </CollapsibleContent>
    </Collapsible>
  )
}

export function FileTree({ files, download }: { files: WorkspaceFileEntry[]; download: (path: string) => void }) {
  const roots = new Map<string, TreeNode>()
  for (const { path } of files) {
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
    level.set(name, { type: 'file', name, path })
  }
  return (
    <div className="min-w-0">
      {[...roots].map(([name, node]) => <FileTreeNode key={name} node={node} download={download} />)}
    </div>
  )
}
