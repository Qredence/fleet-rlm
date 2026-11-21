import { useEffect, useState } from 'react'
import { Brain, Check, Pencil, Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Textarea } from '@/components/ui/textarea'
import { errorMessage, type FleetApi, type Memory, type MemoryEdit } from '@/lib/api'

function MemoryCard({ memory, api, onChange }: {
  memory: Memory
  api: FleetApi
  onChange: (memory: Memory | null) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')

  async function update(changes: MemoryEdit | null) {
    setWorking(true)
    setError('')
    try {
      if (changes) onChange(await api.editMemory(memory.id, changes))
      else {
        await api.deleteMemory(memory.id)
        onChange(null)
      }
      setDraft(null)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setWorking(false)
    }
  }

  return (
    <article className={`grid gap-3 rounded-lg border p-3 ${memory.approved ? 'bg-card' : 'border-amber-200 bg-amber-50/70'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={memory.approved ? 'secondary' : 'outline'} className={memory.approved ? '' : 'border-amber-300 text-amber-900'}>
          {memory.approved ? 'Approved' : 'Jev candidate'}
        </Badge>
        <span className="text-xs text-muted-foreground">{memory.session_id ? 'Session memory' : 'Global memory'}</span>
      </div>
      {draft !== null ? (
        <form className="grid gap-2" onSubmit={(event) => {
          event.preventDefault()
          if (draft.trim()) void update({ content: draft.trim() })
        }}>
          <Textarea value={draft} aria-label="Memory content" onChange={(event) => setDraft(event.target.value)} />
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={working || !draft.trim()}>Save memory</Button>
            <Button variant="ghost" size="sm" disabled={working} onClick={() => setDraft(null)}>Cancel</Button>
          </div>
        </form>
      ) : (
        <>
          <p className="whitespace-pre-wrap wrap-break-word text-sm">{memory.content}</p>
          {deleting ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm">Delete this memory?</span>
              <Button variant="destructive" size="sm" disabled={working} onClick={() => void update(null)}>Delete</Button>
              <Button variant="ghost" size="sm" disabled={working} onClick={() => setDeleting(false)}>Cancel</Button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {!memory.approved && <Button size="sm" variant="outline" disabled={working} onClick={() => void update({ approved: true })}><Check /> Approve</Button>}
              <Button size="sm" variant="ghost" disabled={working} onClick={() => setDraft(memory.content)}><Pencil /> Edit</Button>
              <Button size="sm" variant="ghost" disabled={working} onClick={() => setDeleting(true)}><Trash2 /> Delete</Button>
            </div>
          )}
        </>
      )}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </article>
  )
}

function MemoryList({ api, sessionId }: { api: FleetApi; sessionId?: string }) {
  const [memories, setMemories] = useState<Memory[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const request = new AbortController()
    void api.listMemories({ session_id: sessionId }, request.signal).then(setMemories).catch((failure) => {
      if (!request.signal.aborted) setError(errorMessage(failure))
    })
    return () => request.abort()
  }, [api, sessionId])

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="grid gap-2 pr-2 pb-2">
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : memories === null ? (
          <p role="status" className="text-sm text-muted-foreground">Loading memories…</p>
        ) : !memories.length ? <p className="text-sm text-muted-foreground">No memories yet.</p> : (
          memories.map((memory) => <MemoryCard key={memory.id} memory={memory} api={api} onChange={(updated) => {
            setMemories((current) => current?.flatMap((item) => item.id !== memory.id ? [item] : updated ? [updated] : []) ?? null)
          }} />)
        )}
      </div>
    </ScrollArea>
  )
}

export function MemoryDrawer({ api, sessionId }: { api: FleetApi; sessionId?: string }) {
  const [open, setOpen] = useState(false)
  const [sessionOnly, setSessionOnly] = useState(false)
  const filter = sessionOnly ? sessionId : undefined
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="ghost" size="sm" />}><Brain /> <span className="max-sm:sr-only">Memories</span></DialogTrigger>
      <DialogContent className="inset-y-0 right-0 left-auto flex h-dvh w-full max-w-md translate-x-0 translate-y-0 flex-col rounded-none sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Memories</DialogTitle>
          <DialogDescription>Review Jev candidates and manage saved memories.</DialogDescription>
        </DialogHeader>
        {sessionId && (
          <div className="flex gap-2">
            <Button variant={sessionOnly ? 'outline' : 'secondary'} size="sm" aria-pressed={!sessionOnly} onClick={() => setSessionOnly(false)}>All memories</Button>
            <Button variant={sessionOnly ? 'secondary' : 'outline'} size="sm" aria-pressed={sessionOnly} onClick={() => setSessionOnly(true)}>This session</Button>
          </div>
        )}
        {open && <MemoryList key={filter ?? 'all'} api={api} sessionId={filter} />}
      </DialogContent>
    </Dialog>
  )
}
