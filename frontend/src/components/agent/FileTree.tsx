import { ChevronRight, Download, FileText, Folder } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { buildFileTree, formatFileSize, type FileTreeNodeData } from '@/lib/file-tree'
import type { WorkspaceFileEntry } from '@/lib/messages'

type FileActions = { preview: (path: string) => void; download: (path: string) => void }

function FileTreeNode({ node, preview, download }: FileActions & { node: FileTreeNodeData }) {
  if (node.type === 'file') {
    return (
      <div className="flex min-w-0 items-center gap-0.5">
        <Button
          className="min-w-0 flex-1 justify-start text-left"
          variant="ghost"
          size="sm"
          title={`${node.path} (${formatFileSize(node.sizeBytes)})`}
          aria-label={`Preview ${node.path}`}
          onClick={() => preview(node.path)}
        >
          <FileText data-icon="inline-start" />
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
            {formatFileSize(node.sizeBytes)}
          </span>
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          title={`Download ${node.name}`}
          aria-label={`Download ${node.path}`}
          onClick={() => download(node.path)}
        >
          <Download />
        </Button>
      </div>
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
          <FileTreeNode key={name} node={child} preview={preview} download={download} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  )
}

export function FileTree({ files, preview, download }: FileActions & { files: WorkspaceFileEntry[] }) {
  const roots = buildFileTree(files)
  return (
    <div className="grid min-w-0 gap-0.5">
      {[...roots].map(([name, node]) => <FileTreeNode key={name} node={node} preview={preview} download={download} />)}
    </div>
  )
}
