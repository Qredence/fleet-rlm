import { Brain, Check, ChevronDown, Pencil, RefreshCw, Search, Trash2, Undo2 } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { PageHeader } from '@/components/PageHeader'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Item, ItemContent, ItemGroup } from '@/components/ui/item'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { errorMessage, type FleetApi, type Memory, type MemoryEdit, type MemoryFilters, type Session } from '@/lib/api'

const followUpDelays = [2500, 6000]
const memoryLimit = 100
const maxMemoryLimit = 500
const scopeLabels: Record<string, string> = { global: 'Global memories', session: 'All session memories' }

export function MemoriesPage({ api, sessions, completedAt }: {
  api: FleetApi
  sessions: Session[]
  completedAt: number
}) {
  const [params, setParams] = useSearchParams()
  const sessionId = params.get('session_id') ?? ''
  const scopeParam = params.get('scope')
  const scope = scopeParam === 'global' || scopeParam === 'session' ? scopeParam : undefined
  const kindParam = params.get('kind')
  const kind = kindParam === 'fact' || kindParam === 'procedure' ? kindParam : undefined
  const search = params.get('q')?.trim() ?? ''
  const memoryId = params.get('memory_id') ?? ''
  const approval = params.get('approved')
  const approved = approval === 'true' ? true : approval === 'false' ? false : undefined
  const scopeValue = scope ?? sessionId
  const scopedSession = sessions.find(({ id }) => id === sessionId)
  const scopeLabel = scope ? scopeLabels[scope] : scopedSession?.title ?? (sessionId ? 'Linked session' : 'All scopes')

  function changeFilter(key: string, value: string) {
    const next = new URLSearchParams(params)
    next.delete('memory_id')
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }

  function changeScope(value: string) {
    const next = new URLSearchParams(params)
    next.delete('memory_id')
    next.delete('scope')
    next.delete('session_id')
    if (value === 'global' || value === 'session') next.set('scope', value)
    else if (value) next.set('session_id', value)
    setParams(next)
  }

  return (
    <>
      <PageHeader title="Memories" />
      <ScrollArea className="min-h-0 flex-1">
        <section className="mx-auto flex w-full max-w-4xl flex-col gap-5 p-4 sm:p-7" aria-label="Memory review">
          <MemorySearch key={search} initial={search} onSearch={(value) => changeFilter('q', value)} />
          <div className="flex flex-wrap items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="outline" size="sm" className="max-w-full" aria-label={`Memory scope: ${scopeLabel}`} />}>
                <span className="truncate">{scopeLabel}</span><ChevronDown data-icon="inline-end" />
              </DropdownMenuTrigger>
              <DropdownMenuContent className="max-w-[min(20rem,calc(100vw-2rem))]">
                <DropdownMenuRadioGroup value={scopeValue} onValueChange={changeScope}>
                  <DropdownMenuRadioItem value="">All sessions and global</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="global">{scopeLabels.global}</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="session">{scopeLabels.session}</DropdownMenuRadioItem>
                  {sessionId && !scopedSession && <DropdownMenuRadioItem value={sessionId}>Linked session</DropdownMenuRadioItem>}
                  {sessions.map(({ id, title }) => <DropdownMenuRadioItem key={id} value={id}><span className="truncate">{title}</span></DropdownMenuRadioItem>)}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            <ToggleGroup
              aria-label="Filter by approval" variant="outline" size="sm"
              value={[approval === 'true' || approval === 'false' ? approval : 'all']}
              onValueChange={([value]) => { if (value) changeFilter('approved', value === 'all' ? '' : value) }}
            >
              <ToggleGroupItem value="all">All statuses</ToggleGroupItem>
              <ToggleGroupItem value="false">Pending</ToggleGroupItem>
              <ToggleGroupItem value="true">Approved</ToggleGroupItem>
            </ToggleGroup>
            <ToggleGroup
              aria-label="Filter by kind" variant="outline" size="sm"
              value={[kind ?? 'all']}
              onValueChange={([value]) => { if (value) changeFilter('kind', value === 'all' ? '' : value) }}
            >
              <ToggleGroupItem value="all">All kinds</ToggleGroupItem>
              <ToggleGroupItem value="fact">Facts</ToggleGroupItem>
              <ToggleGroupItem value="procedure">Procedures</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <MemoryList
            key={`${scopeValue}:${approved}:${kind}:${search}:${memoryId}`} api={api}
            filters={{ session_id: sessionId || undefined, scope, approved, kind, q: search || undefined, memory_id: memoryId || undefined }}
            onShowAll={() => changeFilter('memory_id', '')}
            sessions={sessions} completedAt={completedAt}
          />
        </section>
      </ScrollArea>
    </>
  )
}

function MemorySearch({ initial, onSearch }: { initial: string; onSearch: (value: string) => void }) {
  const [draft, setDraft] = useState(initial)

  return (
    <form role="search" onSubmit={(event) => {
      event.preventDefault()
      onSearch(draft.trim())
    }}>
      <InputGroup>
        <InputGroupInput
          type="search" aria-label="Search memories" placeholder="Search memories…"
          value={draft} onChange={(event) => {
            setDraft(event.target.value)
            if (!event.target.value && initial) onSearch('')
          }}
        />
        <InputGroupAddon><Search aria-hidden="true" /></InputGroupAddon>
      </InputGroup>
    </form>
  )
}

function MemoryList({ api, filters, onShowAll, sessions, completedAt }: {
  api: FleetApi
  filters: Omit<MemoryFilters, 'before_id' | 'limit'>
  onShowAll: () => void
  sessions: Session[]
  completedAt: number
}) {
  const [rows, setRows] = useState<Memory[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [attempt, retry] = useState(0)
  const request = useRef<AbortController | null>(null)
  const loaded = useRef(0)
  const { session_id: sessionId, scope, approved, kind, q: search, memory_id: memoryId } = filters
  const filtered = Object.entries(filters).some(([key, value]) => key !== 'memory_id' && value !== undefined)

  useEffect(() => {
    function load() {
      request.current?.abort()
      const controller = new AbortController()
      request.current = controller
      const limit = Math.min(maxMemoryLimit, Math.max(memoryLimit, loaded.current))
      const query = { session_id: sessionId, scope, approved, kind, q: search, memory_id: memoryId, limit }
      void api.listMemories(query, controller.signal).then((items) => {
        if (controller.signal.aborted) return
        loaded.current = items.length
        setRows(items)
        setHasMore(!search && items.length === limit)
        setError('')
      }).catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure))
      })
    }

    load()
    const elapsed = Date.now() - completedAt
    const timers = followUpDelays.filter((delay) => delay > elapsed)
      .map((delay) => window.setTimeout(load, delay - elapsed))
    return () => {
      request.current?.abort()
      timers.forEach((timer) => window.clearTimeout(timer))
    }
  }, [api, sessionId, scope, approved, kind, search, memoryId, attempt, completedAt])

  function loadMore() {
    const last = rows?.at(-1)
    if (!last) return
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoadingMore(true)
    void api.listMemories({ ...filters, before_id: last.id, limit: memoryLimit }, controller.signal).then((items) => {
      if (controller.signal.aborted) return
      setRows((current) => {
        const next = [...current ?? [], ...items]
        loaded.current = next.length
        return next
      })
      setHasMore(items.length === memoryLimit)
      setError('')
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(errorMessage(failure))
    }).finally(() => setLoadingMore(false))
  }

  return (
    <>
      <div className="flex items-center justify-between gap-2">
        {memoryId ? (
          <Button variant="outline" size="sm" onClick={onShowAll}>Show all in this scope</Button>
        ) : <p className="text-xs text-muted-foreground">{search ? `Showing up to ${memoryLimit} best matches.` : 'Showing the newest memories first.'}</p>}
        <Tooltip>
          <TooltipTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Refresh memories" onClick={() => retry((value) => value + 1)} />}>
            <RefreshCw />
          </TooltipTrigger>
          <TooltipContent>Refresh memories</TooltipContent>
        </Tooltip>
      </div>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {rows === null ? (
        !error && (
          <div role="status" aria-label="Loading memories" className="grid gap-3">
            <Skeleton className="h-28 w-full motion-reduce:animate-none" />
            <Skeleton className="h-28 w-full motion-reduce:animate-none" />
          </div>
        )
      ) : !rows.length ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Brain /></EmptyMedia>
            <EmptyTitle>{memoryId ? 'Lesson unavailable' : 'No memories found'}</EmptyTitle>
            {!memoryId && <EmptyDescription>{filtered ? 'Change the filters to see more memories.' : 'Memory candidates appear here after conversations.'}</EmptyDescription>}
          </EmptyHeader>
        </Empty>
      ) : (
        <ItemGroup aria-label="Memories">
          {rows.map((memory) => (
            <MemoryRow
              key={memory.id} memory={memory} api={api}
              sessionTitle={sessions.find(({ id }) => id === memory.session_id)?.title}
              onChange={(changed) => {
                request.current?.abort()
                setRows((current) => current?.flatMap((row) => {
                  if (row.id !== memory.id) return [row]
                  return changed && (approved === undefined || approved === changed.approved) ? [changed] : []
                }) ?? null)
                retry((value) => value + 1)
              }}
            />
          ))}
        </ItemGroup>
      )}
      {rows && hasMore && (
        <Button variant="outline" className="self-center" disabled={loadingMore} onClick={loadMore}>
          {loadingMore ? 'Loading…' : 'Load more memories'}
        </Button>
      )}
    </>
  )
}

function MemoryRow({ memory, sessionTitle, api, onChange }: {
  memory: Memory
  sessionTitle?: string
  api: FleetApi
  onChange: (memory: Memory | null) => void
}) {
  const inputId = useId()
  const [draft, setDraft] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')

  async function save(changes: MemoryEdit | null) {
    if (working) return
    setWorking(true)
    setError('')
    try {
      if (changes) onChange(await api.editMemory(memory.id, changes))
      else {
        await api.deleteMemory(memory.id)
        onChange(null)
      }
      setDraft(null)
      setDeleting(false)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setWorking(false)
    }
  }

  return (
    <Item role="listitem" variant="outline" className="items-start p-4">
      <ItemContent className="min-w-0 gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {memory.session_id ? (
            <Link to={`/sessions/${memory.session_id}`} className="min-w-0 truncate text-xs text-muted-foreground underline-offset-4 hover:underline">
              {sessionTitle ?? 'Session memory'}
            </Link>
          ) : <span className="text-xs text-muted-foreground">Global memory</span>}
          <Badge variant={memory.approved ? 'secondary' : 'outline'}>
            {memory.approved ? 'Approved' : 'Pending review'}
          </Badge>
          {memory.source_run_id && (
            <Badge variant="outline">{memory.skill_name ? `Procedure · @${memory.skill_name}` : 'Procedure'}</Badge>
          )}
        </div>
        {draft !== null ? (
          <form className="flex flex-col gap-2" onSubmit={(event) => {
            event.preventDefault()
            if (draft.trim()) void save({ content: draft.trim() })
          }}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor={inputId}>Memory content</FieldLabel>
                <Textarea id={inputId} autoFocus value={draft} onChange={(event) => setDraft(event.target.value)} />
              </Field>
            </FieldGroup>
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={working || !draft.trim()}>Save</Button>
              <Button type="button" size="sm" variant="ghost" disabled={working} onClick={() => setDraft(null)}>Cancel</Button>
            </div>
          </form>
        ) : (
          <>
            <p className="whitespace-pre-wrap wrap-anywhere leading-relaxed">{memory.content}</p>
            <div className="flex flex-wrap items-center gap-1">
              <Button variant="outline" size="sm" disabled={working} onClick={() => void save({ approved: !memory.approved })}>
                {memory.approved ? <Undo2 data-icon="inline-start" /> : <Check data-icon="inline-start" />}
                {memory.approved ? 'Revoke approval' : 'Approve'}
              </Button>
              {memory.approved && memory.source_run_id && memory.session_id && (
                <Button variant="outline" size="sm" render={
                  <Link to={`/sessions/${memory.session_id}`} state={{ skillDraft: skillDraftPrompt(memory) }} />
                }>
                  Draft a skill
                </Button>
              )}
              <Button variant="ghost" size="sm" disabled={working} onClick={() => setDraft(memory.content)}>
                <Pencil data-icon="inline-start" /> Edit
              </Button>
              <AlertDialog open={deleting} onOpenChange={(open) => { if (!working) setDeleting(open) }}>
                <AlertDialogTrigger render={<Button variant="ghost" size="sm" disabled={working} />}>
                  <Trash2 data-icon="inline-start" /> Delete
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete memory?</AlertDialogTitle>
                    <AlertDialogDescription>This permanently removes it from memory review and future recall.</AlertDialogDescription>
                  </AlertDialogHeader>
                  {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
                  <AlertDialogFooter>
                    <AlertDialogCancel disabled={working}>Cancel</AlertDialogCancel>
                    <Button variant="destructive" disabled={working} onClick={() => void save(null)}>Delete</Button>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </>
        )}
        {error && !deleting && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      </ItemContent>
    </Item>
  )
}

function skillDraftPrompt(memory: Memory) {
  const dependency = memory.skill_name ? ` Document any dependency on the existing ${memory.skill_name} skill.` : ''
  return `@skill-creator Draft an installable Fleet skill from the approved procedure below. Make it reusable and include a verification step.${dependency} Write a SKILL.md artifact for review.\n\n${memory.content}`
}
