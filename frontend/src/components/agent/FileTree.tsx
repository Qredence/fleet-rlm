import { ChevronRight, Download, FileText, Folder } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { buildFileTree, formatFileSize, type FileTreeNodeData } from '@/lib/file-tree'
import type { WorkspaceFileEntry } from '@/lib/messages'

function FileTreeNode({ node, download }: { node: FileTreeNodeData; download: (path: string) => void }) {
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
  const roots = buildFileTree(files)
  return (
    <div className="grid min-w-0 gap-0.5">
      {[...roots].map(([name, node]) => <FileTreeNode key={name} node={node} download={download} />)}
    </div>
  )
}
