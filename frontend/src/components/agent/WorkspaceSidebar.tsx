import { useRef, useState } from 'react'
import { FolderOpen, MessageSquare, Plus, Trash2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { Session, WorkspaceFile } from '@/lib/api'
import { FileTree } from './FileTree'

export interface WorkspaceSidebarProps {
  session?: Session
  sessions: Session[]
  sessionsLoading: boolean
  changingSessions: boolean
  files: WorkspaceFile[]
  filesLoading: boolean
  uploading: boolean
  error: string
  onCreate: (title?: string) => Promise<boolean>
  onDelete: (id: string) => Promise<boolean>
  onSelect: (id: string) => void
  onDownload: (path: string) => void
  onUpload: (file: File) => void
}

export function WorkspaceSidebar({ session, sessions, sessionsLoading, changingSessions, files, filesLoading, uploading, error, onCreate, onDelete, onSelect, onUpload, onDownload }: WorkspaceSidebarProps) {
  const [dialog, setDialog] = useState<'create' | 'delete' | null>(null)
  const [title, setTitle] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  return (
    <aside aria-label="Workspace" className="flex h-full min-h-0 w-full flex-col bg-sidebar px-3 py-4 text-sidebar-foreground">
      <div className="flex items-center justify-between gap-2 px-1 pb-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Sessions</h2>
        <Dialog open={dialog === 'create'} onOpenChange={(open) => {
          if (open) setTitle('')
          setDialog(open ? 'create' : null)
        }}>
          <DialogTrigger render={<Button size="icon-sm" variant="ghost" aria-label="New session" disabled={sessionsLoading || changingSessions} />}>
            <Plus />
          </DialogTrigger>
          <DialogContent>
            <form className="grid gap-4" onSubmit={async (event) => {
              event.preventDefault()
              if (await onCreate(title.trim() || undefined)) setDialog(null)
            }}>
              <DialogHeader>
                <DialogTitle>New session</DialogTitle>
                <DialogDescription>Create a workspace for a new conversation.</DialogDescription>
              </DialogHeader>
              <label className="grid gap-1.5 text-sm font-medium">
                Session title
                <input className="field" placeholder="New session" value={title} onChange={(event) => setTitle(event.target.value)} />
              </label>
              {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
              <DialogFooter>
                <Button type="submit" disabled={changingSessions}>Create session</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
      <ScrollArea className="min-h-0 shrink basis-[32%]">
        <nav className="grid gap-0.5" aria-label="Sessions">
          {sessions.map((item) => (
            <Button
              key={item.id}
              size="sm"
              variant="ghost"
              aria-current={item.id === session?.id ? 'page' : undefined}
              disabled={changingSessions}
              className="w-full justify-start font-normal aria-[current=page]:bg-sidebar-accent aria-[current=page]:font-medium"
              onClick={() => onSelect(item.id)}
              title={item.title}
            >
              <MessageSquare className="shrink-0 text-muted-foreground" />
              <span className="min-w-0 truncate">{item.title}</span>
            </Button>
          ))}
          {!sessions.length && (
            <p className="px-2 py-2 text-xs text-muted-foreground">{sessionsLoading ? 'Loading sessions…' : 'No sessions yet'}</p>
          )}
        </nav>
      </ScrollArea>
      <section className="mt-4 flex min-h-0 flex-1 flex-col border-t pt-3" aria-label="Workspace files">
        <div className="flex items-center justify-between gap-2 px-1 pb-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Files</h2>
          <Button size="icon-sm" variant="ghost" aria-label="Upload file" disabled={!session || uploading} onClick={() => fileInput.current?.click()}>
            <Upload />
          </Button>
          <input ref={fileInput} type="file" className="hidden" aria-label="Choose a workspace file" onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (file) onUpload(file)
          }} />
        </div>
        <ScrollArea className="min-h-0 flex-1">
          {files.length ? <FileTree files={files} download={onDownload} /> : (
            <p className="flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground">
              <FolderOpen className="size-4" /> {filesLoading ? 'Loading files…' : 'No workspace files'}
            </p>
          )}
        </ScrollArea>
        {uploading && <p role="status" className="px-2 py-1 text-xs text-muted-foreground">Uploading file…</p>}
        {error && !dialog && <p role="alert" className="px-2 py-1 text-xs text-destructive">{error}</p>}
      </section>
      {session && (
        <Dialog open={dialog === 'delete'} onOpenChange={(open) => setDialog(open ? 'delete' : null)}>
          <DialogTrigger render={<Button size="sm" variant="ghost" className="mt-3 justify-start text-muted-foreground" disabled={changingSessions} />}>
            <Trash2 /> Delete session
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete {session.title}?</DialogTitle>
              <DialogDescription>This permanently deletes the conversation and its workspace files.</DialogDescription>
            </DialogHeader>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
              <Button variant="destructive" disabled={changingSessions} onClick={async () => {
                if (await onDelete(session.id)) setDialog(null)
              }}>Delete session</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </aside>
  )
}
