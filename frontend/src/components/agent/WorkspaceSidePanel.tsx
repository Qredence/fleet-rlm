import {
  Brain, Check, FolderOpen, HardDrive, Pencil, RefreshCw, Trash2, Upload, X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import {
  errorMessage, type FleetApi, type Memory, type MemoryEdit, type Session, type WorkspaceFile,
} from '@/lib/api'
import { FileTree } from './FileTree'

type MemoryFilter = 'all' | 'session' | 'pending'

function MemoryCard({
  memory,
  currentSessionId,
  api,
  onChange,
}: {
  memory: Memory
  currentSessionId?: string
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

  const scopeLabel = !memory.session_id
    ? 'Global'
    : memory.session_id === currentSessionId
      ? 'This session'
      : 'Session'

  return (
    <article
      className={`grid gap-2.5 rounded-xl border p-3 transition-colors ${
        memory.approved ? 'border-border bg-card' : 'border-foreground/20 bg-muted/50'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-1.5">
        <Badge
          variant={memory.approved ? 'secondary' : 'outline'}
          className="rounded-full font-normal"
        >
          {memory.approved ? 'Approved' : 'Pending review'}
        </Badge>
        <span className="text-[11px] text-muted-foreground">{scopeLabel}</span>
      </div>

      {draft !== null ? (
        <form
          className="grid gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (draft.trim()) void update({ content: draft.trim() })
          }}
        >
          <Textarea
            value={draft}
            aria-label="Memory content"
            className="min-h-20 rounded-xl text-xs"
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="flex items-center gap-1.5">
            <Button type="submit" size="xs" disabled={working || !draft.trim()}>Save</Button>
            <Button type="button" variant="ghost" size="xs" disabled={working} onClick={() => setDraft(null)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <>
          <p className="whitespace-pre-wrap wrap-break-word text-xs leading-relaxed text-foreground">
            {memory.content}
          </p>
          {deleting ? (
            <div className="flex flex-wrap items-center gap-1.5 border-t pt-2">
              <span className="mr-auto text-xs text-muted-foreground">Delete memory?</span>
              <Button variant="destructive" size="xs" disabled={working} onClick={() => void update(null)}>
                Delete
              </Button>
              <Button variant="ghost" size="xs" disabled={working} onClick={() => setDeleting(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-1 pt-0.5">
              {!memory.approved && (
                <Button
                  size="xs"
                  variant="outline"
                  disabled={working}
                  onClick={() => void update({ approved: true })}
                >
                  <Check /> Approve
                </Button>
              )}
              <Button size="xs" variant="ghost" disabled={working} onClick={() => setDraft(memory.content)}>
                <Pencil /> Edit
              </Button>
              <Button size="xs" variant="ghost" disabled={working} onClick={() => setDeleting(true)}>
                <Trash2 /> Delete
              </Button>
            </div>
          )}
        </>
      )}

      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </article>
  )
}

export interface WorkspaceSidePanelProps {
  api: FleetApi
  session?: Session
  files: WorkspaceFile[]
  filesLoading: boolean
  uploading: boolean
  fileError: string
  memoryRefreshKey: number
  open: boolean
  onClose: () => void
  onUpload: (file: File) => void
  onDownload: (path: string) => void
}

export function WorkspaceSidePanel({
  api,
  session,
  files,
  filesLoading,
  uploading,
  fileError,
  memoryRefreshKey,
  open,
  onClose,
  onUpload,
  onDownload,
}: WorkspaceSidePanelProps) {
  const [activeTab, setActiveTab] = useState<'files' | 'memories'>('files')
  const [dragging, setDragging] = useState(false)
  const [memories, setMemories] = useState<Memory[] | null>(null)
  const [memoriesLoading, setMemoriesLoading] = useState(false)
  const [memoryError, setMemoryError] = useState('')
  const [memoryFilter, setMemoryFilter] = useState<MemoryFilter>('all')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const memoryRequestRef = useRef<AbortController | null>(null)

  const fetchMemories = useCallback((signal?: AbortSignal) => {
    void api.listMemories({}, signal)
      .then((items) => {
        if (!signal?.aborted) setMemories(items)
      })
      .catch((failure) => {
        if (!signal?.aborted) setMemoryError(errorMessage(failure))
      })
      .finally(() => {
        if (!signal?.aborted) setMemoriesLoading(false)
      })
  }, [api])

  const loadMemories = useCallback(() => {
    memoryRequestRef.current?.abort()
    const request = new AbortController()
    memoryRequestRef.current = request
    setMemoriesLoading(true)
    setMemoryError('')
    fetchMemories(request.signal)
  }, [fetchMemories])

  useEffect(() => {
    memoryRequestRef.current?.abort()
    const request = new AbortController()
    memoryRequestRef.current = request
    fetchMemories(request.signal)
    return () => request.abort()
  }, [fetchMemories, memoryRefreshKey])

  const pendingCount = useMemo(
    () => (memories ?? []).filter((item) => !item.approved).length,
    [memories],
  )

  const filteredMemories = useMemo(() => {
    const items = memories ?? []
    if (memoryFilter === 'session') {
      return session ? items.filter((item) => item.session_id === session.id) : []
    }
    if (memoryFilter === 'pending') {
      return items.filter((item) => !item.approved)
    }
    return items
  }, [memories, memoryFilter, session])

  function handleFileList(list: FileList | null) {
    if (!list || !session) return
    for (const file of Array.from(list)) {
      onUpload(file)
    }
  }

  const sandboxId = session?.daytona_sandbox_id ?? null

  return (
    <>
      {open && (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-20 bg-black/25 backdrop-blur-[1px] lg:hidden"
          onClick={onClose}
        />
      )}
      <aside
        aria-label="Workspace panel"
        className={`${
          open ? 'flex' : 'hidden'
        } fixed inset-y-0 right-0 z-30 h-full w-80 max-w-[calc(100vw-2rem)] flex-col border-l bg-sidebar text-sidebar-foreground shadow-xl lg:static lg:z-auto lg:h-auto lg:shrink-0 lg:shadow-none`}
      >
        <Tabs
          value={activeTab}
          onValueChange={(value) => setActiveTab(value as 'files' | 'memories')}
          className="flex min-h-0 flex-1 flex-col gap-0"
        >
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-sidebar-border px-3 py-2.5">
            <TabsList className="h-8 flex-1 rounded-xl p-0.5">
              <TabsTrigger value="files" className="gap-1.5 rounded-lg text-xs">
                <FolderOpen className="size-3.5" />
                Files
                <span className="rounded-full bg-muted px-1.5 py-0.2 font-mono text-[10px] tabular-nums text-muted-foreground">
                  {files.length}
                </span>
              </TabsTrigger>
              <TabsTrigger value="memories" className="gap-1.5 rounded-lg text-xs">
                <Brain className="size-3.5" />
                Memories
                <span className="rounded-full bg-muted px-1.5 py-0.2 font-mono text-[10px] tabular-nums text-muted-foreground">
                  {pendingCount > 0 ? `${pendingCount}` : (memories?.length ?? 0)}
                </span>
              </TabsTrigger>
            </TabsList>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              className="shrink-0 lg:hidden"
              aria-label="Close workspace panel"
              onClick={onClose}
            >
              <X />
            </Button>
          </div>

          <TabsContent value="files" className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="grid shrink-0 gap-2.5 p-3 pb-2">
              <div className="grid gap-1.5 rounded-xl border bg-card p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className={`size-2 shrink-0 rounded-full ${
                        sandboxId ? 'bg-foreground' : 'bg-muted-foreground/35'
                      }`}
                      aria-hidden="true"
                    />
                    <span className="truncate text-xs font-medium text-foreground">
                      {sandboxId ? 'Sandbox active' : 'Starts on first run'}
                    </span>
                  </div>
                  <Badge variant="outline" className="gap-1 rounded-full font-mono text-[10px]">
                    <HardDrive className="size-2.5" />
                    /workspace
                  </Badge>
                </div>
                <p className="truncate font-mono text-[11px] text-muted-foreground" title={sandboxId ?? undefined}>
                  {sandboxId ? `Daytona · ${sandboxId}` : 'Daytona isolated sandbox · persistent volume'}
                </p>
              </div>

              <div
                onDragEnter={(event) => {
                  event.preventDefault()
                  if (session && !uploading) setDragging(true)
                }}
                onDragOver={(event) => {
                  event.preventDefault()
                  if (session && !uploading) setDragging(true)
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    setDragging(false)
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault()
                  setDragging(false)
                  if (session && !uploading) handleFileList(event.dataTransfer.files)
                }}
                className={`rounded-xl border border-dashed transition-colors ${
                  dragging
                    ? 'border-foreground bg-muted/80'
                    : 'border-border bg-card/60 hover:border-foreground/35 hover:bg-muted/40'
                }`}
              >
                <button
                  type="button"
                  disabled={!session || uploading}
                  onClick={() => fileInputRef.current?.click()}
                  className="flex w-full flex-col items-center justify-center gap-1 px-3 py-4 text-center outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Upload className="size-4 text-muted-foreground" />
                  <span className="text-xs font-medium text-foreground">
                    {uploading ? 'Uploading file…' : 'Drop files or click to upload'}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    Mounted into /workspace for this session
                  </span>
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  aria-label="Upload workspace file"
                  onChange={(event) => {
                    handleFileList(event.target.files)
                    event.target.value = ''
                  }}
                />
              </div>

              {fileError && <p role="alert" className="text-xs text-destructive">{fileError}</p>}
            </div>

            <ScrollArea className="min-h-0 flex-1">
              <div className="px-3 pb-3">
                {files.length ? (
                  <FileTree files={files} download={onDownload} />
                ) : (
                  <div className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-border/70 px-4 py-8 text-center">
                    <FolderOpen className="size-4 text-muted-foreground" />
                    <p className="text-xs text-muted-foreground">
                      {filesLoading ? 'Loading files…' : 'No workspace files yet.'}
                    </p>
                  </div>
                )}
              </div>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="memories" className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex shrink-0 items-center justify-between gap-1.5 px-3 pt-3 pb-2">
              <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Filter memories">
                {(
                  [
                    { id: 'all', label: 'All' },
                    { id: 'session', label: 'This session', disabled: !session },
                    { id: 'pending', label: pendingCount > 0 ? `Pending (${pendingCount})` : 'Pending' },
                  ] as const
                ).map((pill) => {
                  const active = memoryFilter === pill.id
                  return (
                    <Button
                      key={pill.id}
                      type="button"
                      size="xs"
                      variant={active ? 'default' : 'outline'}
                      aria-pressed={active}
                      disabled={'disabled' in pill && pill.disabled}
                      className="rounded-full"
                      onClick={() => setMemoryFilter(pill.id)}
                    >
                      {pill.label}
                    </Button>
                  )
                })}
              </div>
              <Button
                type="button"
                size="icon-xs"
                variant="ghost"
                aria-label="Refresh memories"
                disabled={memoriesLoading}
                onClick={loadMemories}
              >
                <RefreshCw className={memoriesLoading ? 'animate-spin' : ''} />
              </Button>
            </div>

            <ScrollArea className="min-h-0 flex-1">
              <div className="grid gap-2 px-3 pb-3">
                {memoryError ? (
                  <p role="alert" className="text-xs text-destructive">{memoryError}</p>
                ) : memories === null ? (
                  <p role="status" className="px-1 py-4 text-center text-xs text-muted-foreground">
                    Loading memories…
                  </p>
                ) : !filteredMemories.length ? (
                  <div className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-border/70 px-4 py-8 text-center">
                    <Brain className="size-4 text-muted-foreground" />
                    <p className="text-xs text-muted-foreground">
                      {memoryFilter === 'pending'
                        ? 'No pending memory candidates.'
                        : memoryFilter === 'session'
                          ? 'No memories saved for this session.'
                          : 'No memories yet.'}
                    </p>
                  </div>
                ) : (
                  filteredMemories.map((memory) => (
                    <MemoryCard
                      key={memory.id}
                      memory={memory}
                      currentSessionId={session?.id}
                      api={api}
                      onChange={(updated) => {
                        setMemories((current) =>
                          current?.flatMap((item) => (item.id !== memory.id ? [item] : updated ? [updated] : [])) ?? null,
                        )
                      }}
                    />
                  ))
                )}
              </div>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </aside>
    </>
  )
}
