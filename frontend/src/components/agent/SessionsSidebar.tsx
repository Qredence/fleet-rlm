import { Check, MessageSquare, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { Session } from '@/lib/api'

export interface SessionsSidebarProps {
  session?: Session
  sessions: Session[]
  sessionsLoading: boolean
  changingSessions: boolean
  error: string
  onCreate: (title?: string) => Promise<boolean>
  onRename: (id: string, title: string) => Promise<boolean>
  onDelete: (id: string) => Promise<boolean>
  onSelect: (id: string) => void
}

export function SessionsSidebar({
  session,
  sessions,
  sessionsLoading,
  changingSessions,
  error,
  onCreate,
  onRename,
  onDelete,
  onSelect,
}: SessionsSidebarProps) {
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [deletingSession, setDeletingSession] = useState<Session | null>(null)

  const filteredSessions = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    if (!normalized) return sessions
    return sessions.filter((item) => item.title.toLowerCase().includes(normalized))
  }, [query, sessions])

  function startRename(item: Session) {
    setEditingId(item.id)
    setRenameDraft(item.title)
  }

  function cancelRename() {
    setEditingId(null)
    setRenameDraft('')
  }

  async function commitRename(id: string) {
    const trimmed = renameDraft.trim()
    if (!trimmed) return
    if (await onRename(id, trimmed)) {
      cancelRename()
    }
  }

  return (
    <aside
      aria-label="Sessions"
      className="flex h-full min-h-0 w-full flex-col bg-sidebar text-sidebar-foreground"
    >
      <div className="grid shrink-0 gap-2.5 border-b border-sidebar-border p-3">
        <Button
          size="default"
          className="h-9 w-full justify-center gap-2 rounded-xl font-medium"
          disabled={sessionsLoading || changingSessions}
          onClick={() => void onCreate()}
        >
          <Plus className="size-4" />
          New session
        </Button>
        <div className="relative">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search sessions…"
            aria-label="Filter sessions"
            className="h-8 rounded-xl bg-background/80 pr-7 pl-8 text-xs"
          />
          {query && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setQuery('')}
              className="absolute top-1/2 right-2 -translate-y-1/2 rounded-md p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <nav className="grid gap-1 p-2" aria-label="Session list">
          {filteredSessions.map((item) => {
            const active = item.id === session?.id
            const editing = editingId === item.id

            if (editing) {
              return (
                <form
                  key={item.id}
                  className="flex items-center gap-1 rounded-xl border border-ring/40 bg-background p-1"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void commitRename(item.id)
                  }}
                >
                  <Input
                    value={renameDraft}
                    autoFocus
                    aria-label="Rename session"
                    disabled={changingSessions}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Escape') cancelRename()
                    }}
                    className="h-7 flex-1 rounded-lg border-0 bg-transparent px-2 text-xs shadow-none focus-visible:ring-0"
                  />
                  <Button
                    type="submit"
                    size="icon-xs"
                    variant="ghost"
                    aria-label="Save session name"
                    disabled={changingSessions || !renameDraft.trim()}
                  >
                    <Check />
                  </Button>
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    aria-label="Cancel rename"
                    disabled={changingSessions}
                    onClick={cancelRename}
                  >
                    <X />
                  </Button>
                </form>
              )
            }

            return (
              <div
                key={item.id}
                className={`group flex min-w-0 items-center gap-1 rounded-xl px-1.5 py-1 transition-colors ${
                  active
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                    : 'hover:bg-sidebar-accent/60'
                }`}
              >
                <button
                  type="button"
                  aria-current={active ? 'page' : undefined}
                  disabled={changingSessions}
                  onClick={() => onSelect(item.id)}
                  onDoubleClick={() => startRename(item)}
                  title={item.title}
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 py-1 text-left text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                >
                  <MessageSquare
                    className={`size-3.5 shrink-0 ${active ? 'text-foreground' : 'text-muted-foreground'}`}
                  />
                  <span className={`min-w-0 flex-1 truncate ${active ? 'font-medium' : 'font-normal'}`}>
                    {item.title}
                  </span>
                </button>
                <div
                  className={`flex shrink-0 items-center gap-0.5 transition-opacity ${
                    active ? 'opacity-100' : 'opacity-0 group-focus-within:opacity-100 group-hover:opacity-100'
                  }`}
                >
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    aria-label={`Rename ${item.title}`}
                    disabled={changingSessions}
                    onClick={() => startRename(item)}
                  >
                    <Pencil />
                  </Button>
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    aria-label={`Delete ${item.title}`}
                    disabled={changingSessions}
                    onClick={() => setDeletingSession(item)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            )
          })}

          {!filteredSessions.length && (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              {sessionsLoading
                ? 'Loading sessions…'
                : sessions.length
                  ? 'No sessions match your search.'
                  : 'No sessions yet.'}
            </p>
          )}
        </nav>
      </ScrollArea>

      {error && !deletingSession && (
        <div className="shrink-0 border-t border-sidebar-border px-3 py-2">
          <p role="alert" className="text-xs text-destructive">{error}</p>
        </div>
      )}

      <Dialog
        open={deletingSession !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingSession(null)
        }}
      >
        <DialogContent className="rounded-xl">
          <DialogHeader>
            <DialogTitle>Delete “{deletingSession?.title}”?</DialogTitle>
            <DialogDescription>
              This permanently deletes the conversation and its workspace files.
            </DialogDescription>
          </DialogHeader>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
            <Button
              variant="destructive"
              disabled={changingSessions || !deletingSession}
              onClick={async () => {
                if (deletingSession && (await onDelete(deletingSession.id))) {
                  setDeletingSession(null)
                }
              }}
            >
              Delete session
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
