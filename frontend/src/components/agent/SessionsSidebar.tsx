import { Check, MessageSquare, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { cn } from 'cn'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Empty, EmptyHeader, EmptyTitle } from '@/components/ui/empty'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/components/ui/input-group'
import { ScrollArea } from '@/components/ui/scroll-area'
import { errorMessage, type Session } from '@/lib/api'
import { ResponsivePanel, type PanelPresentation } from './ResponsivePanel'

export interface SessionsSidebarProps {
  session?: Session
  sessions: Session[]
  sessionsLoading: boolean
  changingSessions: boolean
  error: string
  presentation: PanelPresentation
  onCreate: (title?: string) => Promise<boolean>
  onRename: (id: string, title: string) => Promise<boolean>
  onDelete: (id: string) => Promise<void>
  onSelect: (id: string) => void
}

export function SessionsSidebar({
  session,
  sessions,
  sessionsLoading,
  changingSessions,
  error,
  presentation,
  onCreate,
  onRename,
  onDelete,
  onSelect,
}: SessionsSidebarProps) {
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [deletingSession, setDeletingSession] = useState<Session | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const deleteTrigger = useRef<HTMLButtonElement | null>(null)
  const newSessionButton = useRef<HTMLButtonElement>(null)
  const cancelDeleteButton = useRef<HTMLButtonElement>(null)

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
    <ResponsivePanel {...presentation} side="left" title="Sessions">
      <div className="flex shrink-0 flex-col gap-2.5 border-b border-sidebar-border p-3">
        <Button
          ref={newSessionButton}
          disabled={sessionsLoading || changingSessions}
          onClick={() => void onCreate()}
        >
          <Plus data-icon="inline-start" />
          New session
        </Button>
        <InputGroup>
          <InputGroupInput
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search sessions…"
            aria-label="Filter sessions"
            className="[&::-webkit-search-cancel-button]:hidden"
          />
          <InputGroupAddon><Search aria-hidden="true" /></InputGroupAddon>
          {query && (
            <InputGroupAddon align="inline-end">
              <InputGroupButton size="icon-xs" aria-label="Clear search" onClick={() => setQuery('')}>
                <X />
              </InputGroupButton>
            </InputGroupAddon>
          )}
        </InputGroup>
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
                  onSubmit={(event) => {
                    event.preventDefault()
                    void commitRename(item.id)
                  }}
                >
                  <InputGroup>
                    <InputGroupInput
                      value={renameDraft}
                      autoFocus
                      aria-label="Rename session"
                      disabled={changingSessions}
                      onChange={(event) => setRenameDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Escape') {
                          event.stopPropagation()
                          cancelRename()
                        }
                      }}
                    />
                    <InputGroupAddon align="inline-end">
                      <InputGroupButton
                        type="submit"
                        size="icon-xs"
                        aria-label="Save session name"
                        disabled={changingSessions || !renameDraft.trim()}
                      >
                        <Check />
                      </InputGroupButton>
                      <InputGroupButton
                        size="icon-xs"
                        aria-label="Cancel rename"
                        disabled={changingSessions}
                        onClick={cancelRename}
                      >
                        <X />
                      </InputGroupButton>
                    </InputGroupAddon>
                  </InputGroup>
                </form>
              )
            }

            return (
              <div
                key={item.id}
                className="group flex min-w-0 items-center gap-1"
              >
                <Button
                  type="button"
                  size="sm"
                  variant={active ? 'secondary' : 'ghost'}
                  aria-current={active ? 'page' : undefined}
                  disabled={changingSessions}
                  onClick={() => onSelect(item.id)}
                  onDoubleClick={() => startRename(item)}
                  title={item.title}
                  className="min-w-0 flex-1 justify-start"
                >
                  <MessageSquare data-icon="inline-start" />
                  <span className="truncate">{item.title}</span>
                </Button>
                <div className={cn(
                  'flex shrink-0 items-center gap-0.5 transition-opacity',
                  !active && '[@media(hover:hover)]:opacity-0 group-focus-within:opacity-100 group-hover:opacity-100',
                )}>
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
                    onClick={(event) => {
                      deleteTrigger.current = event.currentTarget
                      setDeleteError('')
                      setDeletingSession(item)
                    }}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            )
          })}

          {!filteredSessions.length && (
            sessionsLoading ? (
              <p role="status" className="py-6 text-center text-sm text-muted-foreground">Loading sessions…</p>
            ) : (
              <Empty className="p-4">
                <EmptyHeader>
                  <EmptyTitle>{sessions.length ? 'No matching sessions' : 'No sessions yet'}</EmptyTitle>
                </EmptyHeader>
              </Empty>
            )
          )}
        </nav>
      </ScrollArea>

      {error && (
        <div className="shrink-0 p-3">
          <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
        </div>
      )}

      <AlertDialog
        open={deletingSession !== null}
        onOpenChange={(open) => {
          if (!open && !changingSessions) setDeletingSession(null)
        }}
      >
        <AlertDialogContent
          initialFocus={cancelDeleteButton}
          finalFocus={() => deleteTrigger.current?.isConnected ? deleteTrigger.current : newSessionButton.current}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deletingSession?.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes the conversation and its workspace files.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <Alert variant="destructive"><AlertDescription>{deleteError}</AlertDescription></Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel ref={cancelDeleteButton} disabled={changingSessions}>Cancel</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={changingSessions || !deletingSession}
              onClick={async () => {
                if (!deletingSession) return
                setDeleteError('')
                try {
                  await onDelete(deletingSession.id)
                  setDeletingSession(null)
                } catch (failure) {
                  setDeleteError(errorMessage(failure))
                }
              }}
            >
              Delete session
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ResponsivePanel>
  )
}
