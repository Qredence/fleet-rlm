import { ChevronRight, Download, FileText, Folder } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { buildFileTree, formatFileSize, type FileTreeNodeData } from '@/lib/file-tree'
import type { WorkspaceFileEntry } from '@/lib/messages'

function FileTreeNode({ node, download }: { node: FileTreeNodeData; download: (path: string) => void }) {
  if (node.type === 'file') {
    return (
      <Button
        className="group w-full min-w-0 justify-start text-left"
        variant="ghost"
        size="sm"
        title={`${node.path} (${formatFileSize(node.sizeBytes)})`}
        aria-label={`Download ${node.path}`}
        onClick={() => download(node.path)}
      >
        <FileText data-icon="inline-start" />
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
          {formatFileSize(node.sizeBytes)}
        </span>
        <Download data-icon="inline-end" />
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
            className="group w-full justify-start"
          />
        }
      >
        <ChevronRight data-icon="inline-start" className="transition-transform group-aria-expanded:rotate-90" />
        <Folder />
        <span className="truncate">{node.name}</span>
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
