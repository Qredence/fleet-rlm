import { Brain, FolderOpen, Upload } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { cn } from 'cn'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia } from '@/components/ui/empty'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import type { Session, WorkspaceFile } from '@/lib/api'
import { FileTree } from './FileTree'

export function WorkspaceSidePanel({ session, files, error, canUpload, attachments, onUpload, onDownload, onPreview }: {
  session: Session
  files: WorkspaceFile[] | null
  error: string
  canUpload: boolean
  attachments?: ReactNode
  onUpload: (files: File[]) => void
  onDownload: (path: string) => void
  onPreview: (path: string) => void
}) {
  const [dragging, setDragging] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-col gap-3 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-mono text-sm">/workspace</span>
          <Badge variant="outline">{session.daytona_sandbox_id ? 'Sandbox active' : 'Starts on first use'}</Badge>
        </div>
        <div
          className={cn('flex flex-col items-center gap-2 rounded-lg border border-dashed p-3 text-center', dragging && 'border-ring')}
          onDragOver={(event) => {
            if (!event.dataTransfer.types.includes('Files')) return
            event.preventDefault()
            event.dataTransfer.dropEffect = canUpload ? 'copy' : 'none'
            setDragging(canUpload)
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
          }}
          onDrop={(event) => {
            if (!event.dataTransfer.types.includes('Files')) return
            event.preventDefault()
            setDragging(false)
            if (canUpload) onUpload(Array.from(event.dataTransfer.files))
          }}
        >
          <Button size="sm" variant="outline" disabled={!canUpload} onClick={() => picker.current?.click()}>
            <Upload data-icon="inline-start" /> Upload files
          </Button>
          <p className="text-xs text-muted-foreground">Drop files here or attach them in chat.</p>
          <input
            ref={picker}
            type="file"
            aria-label="Upload workspace files"
            multiple
            hidden
            onChange={(event) => {
              const selected = Array.from(event.currentTarget.files ?? [])
              event.currentTarget.value = ''
              if (canUpload) onUpload(selected)
            }}
          />
        </div>
        {attachments}
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="px-3 pb-3">
          {files === null ? (
            <div className="flex flex-col gap-2" role="status" aria-label="Loading files">
              <Skeleton className="h-8 w-full motion-reduce:animate-none" />
              <Skeleton className="h-8 w-3/4 motion-reduce:animate-none" />
            </div>
          ) : files.length ? (
            <FileTree files={files} preview={onPreview} download={onDownload} />
          ) : (
            <Empty className="p-4">
              <EmptyHeader>
                <EmptyMedia variant="icon"><FolderOpen /></EmptyMedia>
                <EmptyDescription>No workspace files yet.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </div>
      </ScrollArea>
      <div className="shrink-0 border-t p-3">
        <Link
          to={`/memories?session_id=${encodeURIComponent(session.id)}`}
          className={buttonVariants({ variant: 'ghost', size: 'sm' })}
        >
          <Brain data-icon="inline-start" /> Session memories
        </Link>
      </div>
    </div>
  )
}
