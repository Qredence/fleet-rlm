import { ChevronRight, Download, FileText, Folder } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cn } from 'cn'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { buildFileTree, formatFileSize, type FileTreeNodeData } from '@/lib/file-tree'
import type { WorkspaceFileEntry } from '@/lib/messages'

type FileActions = { preview: (path: string) => void; download: (path: string) => void }
type TreeDisplay = { compact?: boolean; selectedPath?: string; reveal?: { path: string } }

function FileTreeNode({ node, prefix, compact, selectedPath, reveal, preview, download }:
  FileActions & TreeDisplay & { node: FileTreeNodeData; prefix: string }) {
  const folderPath = `${prefix}${node.name}/`
  const [expanded, setExpanded] = useState(!compact || !prefix || selectedPath?.startsWith(folderPath) === true)

  const [lastReveal, setLastReveal] = useState(reveal)
  if (node.type === 'folder' && lastReveal !== reveal && reveal?.path.startsWith(folderPath)) {
    setLastReveal(reveal)
    setExpanded(true)
  }

  if (node.type === 'file') {
    const selected = selectedPath === node.path
    return (
      <div className={cn('flex min-w-0 items-center gap-0.5 rounded-md', selected && 'bg-accent')}>
        <Button
          className="min-w-0 flex-1 justify-start text-left"
          variant="ghost"
          size={compact ? 'xs' : 'sm'}
          title={`${node.path} (${formatFileSize(node.sizeBytes)})`}
          aria-label={`Preview ${node.path}`}
          aria-current={selected || undefined}
          data-file-path={node.path}
          onClick={() => preview(node.path)}
        >
          <FileText data-icon="inline-start" />
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
          {!compact && (
            <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
              {formatFileSize(node.sizeBytes)}
            </span>
          )}
        </Button>
        <Button
          variant="ghost"
          size={compact ? 'icon-xs' : 'icon-sm'}
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
    <Collapsible open={expanded} onOpenChange={setExpanded}>
      <CollapsibleTrigger
        render={<Button variant="ghost" size={compact ? 'xs' : 'sm'} className="group w-full justify-start" />}
      >
        <ChevronRight className="transition-transform group-aria-expanded:rotate-90" />
        <Folder />
        <span className="truncate">{node.name}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className={compact ? 'ml-2.5 border-l pl-1' : 'ml-3 border-l pl-1.5'}>
        {[...node.children].map(([name, child]) => (
          <FileTreeNode key={name} node={child} prefix={folderPath} compact={compact}
            selectedPath={selectedPath} reveal={reveal} preview={preview} download={download} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  )
}

export function FileTree({ files, preview, download, compact, selectedPath, reveal, focusPath, hidden }:
  FileActions & TreeDisplay & { files: WorkspaceFileEntry[]; focusPath?: string; hidden?: boolean }) {
  const roots = buildFileTree(files)
  const tree = useRef<HTMLDivElement>(null)
  const handledReveal = useRef<typeof reveal>(undefined)
  const handledFocus = useRef<string>(undefined)

  useEffect(() => {
    if (!focusPath) handledFocus.current = undefined
    const shouldFocus = Boolean(focusPath && focusPath !== handledFocus.current)
    const path = shouldFocus ? focusPath : reveal !== handledReveal.current ? reveal?.path : undefined
    if (hidden || !path) return
    let frame: number
    let attempts = 0
    const moveToRow = () => {
      const row = tree.current?.querySelector<HTMLButtonElement>(`[data-file-path="${CSS.escape(path)}"]`)
      if (!row || getComputedStyle(row).visibility !== 'visible') {
        // Base UI can keep the restored tab panel hidden during its transition.
        if (++attempts < 20) frame = requestAnimationFrame(moveToRow)
        return
      }
      // Scroll only the file list. scrollIntoView can also shift the surrounding workspace.
      const viewport = row.closest('[data-slot="scroll-area-viewport"]')
      if (viewport) {
        const { top, bottom } = row.getBoundingClientRect()
        const bounds = viewport.getBoundingClientRect()
        if (top < bounds.top) viewport.scrollTop += top - bounds.top
        else if (bottom > bounds.bottom) viewport.scrollTop += bottom - bounds.bottom
      }
      if (shouldFocus) {
        row.focus({ preventScroll: true })
        handledFocus.current = path
      }
      handledReveal.current = reveal
    }
    frame = requestAnimationFrame(moveToRow)
    return () => cancelAnimationFrame(frame)
  }, [files, focusPath, hidden, reveal])

  return (
    <div ref={tree} className={cn('grid min-w-0 grid-cols-[minmax(0,1fr)]', !compact && 'gap-0.5')}>
      {[...roots].map(([name, node]) => (
        <FileTreeNode key={name} node={node} prefix="" compact={compact}
          selectedPath={selectedPath} reveal={reveal} preview={preview} download={download} />
      ))}
    </div>
  )
}
