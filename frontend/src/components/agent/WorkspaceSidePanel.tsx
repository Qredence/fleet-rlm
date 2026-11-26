import { Archive, ArrowLeft, Brain, FolderOpen, HardDrive, Upload } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { cn } from 'cn'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button, buttonVariants } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia } from '@/components/ui/empty'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsItem, TabsPanel } from '@/components/ui/fluid-tabs'
import type { Session } from '@/lib/api'
import type { WorkspaceFileEntry } from '@/lib/messages'
import { filesInWorkspaceTab, type WorkspaceTab } from '@/lib/workspace-browser'
import { FileTree } from './FileTree'

const workspaceTabs = [
  { value: 'workspace', label: 'Workspace', icon: FolderOpen },
  { value: 'artifacts', label: 'Artifacts', icon: Archive },
] as const

export function WorkspaceSidePanel({
  session, files, error, canUpload, attachments, wide, tab, selectedPath, reveal, focusPath,
  filePreview, onTabChange, onBack, filesToggle, onUpload, onDownload, onPreview,
}: {
  session: Session
  files: WorkspaceFileEntry[] | null
  error: string
  canUpload: boolean
  attachments?: ReactNode
  wide: boolean
  tab: WorkspaceTab
  selectedPath?: string
  reveal?: { path: string }
  focusPath?: string
  filePreview?: ReactNode
  onTabChange: (tab: WorkspaceTab) => void
  onBack: () => void
  filesToggle?: ReactNode
  onUpload: (files: File[]) => void
  onDownload: (path: string) => void
  onPreview: (path: string) => void
}) {
  const [dragging, setDragging] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const back = useRef<HTMLButtonElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const tabsId = useId()
  const selectedIndex = tab === 'workspace' ? 0 : 1
  const previewOnly = !wide && Boolean(selectedPath)
  const showUpload = tab === 'workspace'
  const visibleFiles = files && filesInWorkspaceTab(files, tab)

  useEffect(() => {
    if (previewOnly) back.current?.focus()
  }, [previewOnly])

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className={cn('flex h-(--pane-header-height) shrink-0 items-center gap-2 border-b px-2.5', !wide && 'pr-12')}>
        {previewOnly ? (
          <Button ref={back} variant="ghost" size="sm" onClick={() => {
            // Keep focus inside the Sheet while its Back button is removed.
            content.current?.focus({ preventScroll: true })
            onBack()
          }}>
            <ArrowLeft data-icon="inline-start" /> Back to files
          </Button>
        ) : (
          <>
            <Tabs
              idPrefix={tabsId}
              selectedIndex={selectedIndex}
              onSelect={(index) => onTabChange(workspaceTabs[index].value)}
              size="compact"
              className="workspace-fluid-tabs min-w-0"
              aria-label="Workspace views"
            >
              {workspaceTabs.map(({ value, label, icon }, index) => (
                <TabsItem key={value} index={index} label={label} icon={icon} />
              ))}
            </Tabs>
            <div className="ml-auto flex shrink-0 items-center gap-1">
              {showUpload && (
                <Button variant="ghost" size="icon-sm" aria-label="Upload files" title="Upload files"
                  disabled={!canUpload} onClick={() => picker.current?.click()}>
                  <Upload />
                </Button>
              )}
              {filesToggle}
            </div>
          </>
        )}
        <input
          ref={picker} type="file" aria-label="Upload workspace files" multiple hidden
          onChange={(event) => {
            const selected = Array.from(event.currentTarget.files ?? [])
            event.currentTarget.value = ''
            if (canUpload) onUpload(selected)
          }}
        />
      </div>
      {/* Keep the active panel and preview mounted when a file belongs to both views. */}
      <TabsPanel
        ref={content}
        index={selectedIndex}
        selectedIndex={selectedIndex}
        idPrefix={tabsId}
        aria-labelledby={previewOnly ? undefined : `${tabsId}-tab-${selectedIndex}`}
        aria-label={previewOnly ? 'File preview' : undefined}
        className={cn(
          'relative grid min-h-0 min-w-0 flex-1 overflow-hidden text-sm',
          wide ? 'grid-cols-[minmax(0,1fr)_clamp(11rem,32%,16rem)]' : 'grid-cols-[minmax(0,1fr)]',
        )}
      >
        {(wide || previewOnly) && (
          <div className={cn('flex min-h-0 min-w-0 flex-col overflow-hidden', wide && 'border-r')}>
            {filePreview ?? (
              <Empty className="m-auto p-4">
                <EmptyHeader>
                  <EmptyMedia variant="icon"><FolderOpen /></EmptyMedia>
                  <EmptyDescription>Select a file to preview</EmptyDescription>
                </EmptyHeader>
              </Empty>
            )}
          </div>
        )}
        <div
          className={cn(
            'flex min-h-0 min-w-0 flex-col overflow-hidden bg-background/30',
            previewOnly && 'invisible pointer-events-none absolute inset-0',
            dragging && showUpload && 'ring-1 ring-inset ring-ring',
          )}
          onDragOver={(event) => {
            if (!showUpload || !event.dataTransfer.types.includes('Files')) return
            event.preventDefault()
            event.dataTransfer.dropEffect = canUpload ? 'copy' : 'none'
            setDragging(canUpload)
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
          }}
          onDrop={(event) => {
            if (!showUpload || !event.dataTransfer.types.includes('Files')) return
            event.preventDefault()
            setDragging(false)
            if (canUpload) onUpload(Array.from(event.dataTransfer.files))
          }}
        >
          <div className="flex shrink-0 items-center gap-2 px-2.5 py-2">
            <div className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
              <HardDrive className="size-3.5 shrink-0" aria-hidden="true" />
              <span className="truncate font-mono">{tab === 'artifacts' ? '/workspace/artifacts' : '/workspace'}</span>
            </div>
          </div>
          {(attachments || error) && (
            <div className="px-2.5 pb-2">
              {attachments}
              {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
            </div>
          )}
          <ScrollArea className="min-h-0 flex-1">
            <div className="min-w-0 px-1.5 pb-3">
              {visibleFiles === null ? (
                <div className="flex flex-col gap-2 px-1.5" role="status" aria-label="Loading files">
                  <Skeleton className="h-7 w-full motion-reduce:animate-none" />
                  <Skeleton className="h-7 w-3/4 motion-reduce:animate-none" />
                </div>
              ) : visibleFiles.length ? (
                <FileTree
                  key={tab} files={visibleFiles} preview={onPreview} download={onDownload}
                  compact selectedPath={selectedPath} reveal={reveal} focusPath={focusPath}
                  hidden={previewOnly}
                />
              ) : (
                <Empty className="p-3">
                  <EmptyHeader>
                    <EmptyDescription>{showUpload ? 'No workspace files yet.' : 'No artifacts yet.'}</EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            </div>
          </ScrollArea>
          <div className="shrink-0 border-t p-1.5">
            <Link
              to={`/memories?session_id=${encodeURIComponent(session.id)}`}
              className={buttonVariants({ variant: 'ghost', size: 'sm' })}
            >
              <Brain data-icon="inline-start" /> Session memories
            </Link>
          </div>
        </div>
      </TabsPanel>
      <TabsPanel index={1 - selectedIndex} selectedIndex={selectedIndex} idPrefix={tabsId}>
        {null}
      </TabsPanel>
    </div>
  )
}
