import {
  Brain, Check, FolderOpen, HardDrive, Pencil, RefreshCw, Trash2, Upload,
} from 'lucide-react'
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { cn } from 'cn'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia } from '@/components/ui/empty'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Textarea } from '@/components/ui/textarea'
import {
  errorMessage, type FleetApi, type Memory, type MemoryEdit, type Session, type WorkspaceFile,
} from '@/lib/api'
import { FileTree } from './FileTree'
import { ResponsivePanel, type PanelPresentation } from './ResponsivePanel'

type MemoryFilter = 'all' | 'session' | 'pending'

const MEMORY_FOLLOW_UP_DELAYS_MS = [2500, 6000] as const

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
  const contentId = useId()
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
    <Card size="sm" role="article">
      <CardHeader>
        <CardTitle>{scopeLabel}</CardTitle>
        <Badge variant={memory.approved ? 'secondary' : 'outline'} className="w-fit">
          {memory.approved ? 'Approved' : 'Pending review'}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {draft !== null ? (
          <form
            className="grid gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              if (draft.trim()) void update({ content: draft.trim() })
            }}
          >
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor={contentId}>Memory content</FieldLabel>
                <Textarea
                  id={contentId}
                  value={draft}
                  className="min-h-20"
                  onChange={(event) => setDraft(event.target.value)}
                />
              </Field>
            </FieldGroup>
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
                    <Check data-icon="inline-start" /> Approve
                  </Button>
                )}
                <Button size="xs" variant="ghost" disabled={working} onClick={() => setDraft(memory.content)}>
                  <Pencil data-icon="inline-start" /> Edit
                </Button>
                <Button size="xs" variant="ghost" disabled={working} onClick={() => setDeleting(true)}>
                  <Trash2 data-icon="inline-start" /> Delete
                </Button>
              </div>
            )}
          </>
        )}

        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      </CardContent>
    </Card>
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
  presentation: PanelPresentation
  onUpload: (files: File[]) => void
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
  presentation,
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
  const sessionId = session?.id

  const fetchMemories = useCallback((signal?: AbortSignal) => {
    void api.listMemories({}, signal)
      .then((items) => {
        if (!signal?.aborted) {
          setMemories(items)
          setMemoryError('')
        }
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
    const runRefresh = () => {
      memoryRequestRef.current?.abort()
      const request = new AbortController()
      memoryRequestRef.current = request
      fetchMemories(request.signal)
    }

    runRefresh()

    const timers: number[] = []
    if (memoryRefreshKey > 0) {
      for (const delay of MEMORY_FOLLOW_UP_DELAYS_MS) {
        timers.push(window.setTimeout(runRefresh, delay))
      }
    }

    return () => {
      for (const timer of timers) window.clearTimeout(timer)
      memoryRequestRef.current?.abort()
      memoryRequestRef.current = null
    }
  }, [fetchMemories, memoryRefreshKey, sessionId])

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
    const selected = Array.from(list)
    if (selected.length > 0) onUpload(selected)
  }

  const sandboxId = session?.daytona_sandbox_id ?? null

  return (
    <ResponsivePanel {...presentation} side="right" title="Files & Memories">
      <Tabs
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as 'files' | 'memories')}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-sidebar-border px-3 py-2.5">
          <TabsList className="w-full">
            <TabsTrigger value="files">
              <FolderOpen /> Files
              <Badge variant="secondary">{files.length}</Badge>
            </TabsTrigger>
            <TabsTrigger value="memories">
              <Brain /> Memories
              <Badge variant="secondary">{pendingCount || (memories?.length ?? 0)}</Badge>
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="files" className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="grid shrink-0 gap-2.5 p-3 pb-2">
            <Card size="sm">
              <CardHeader>
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <CardTitle>{sandboxId ? 'Sandbox active' : 'Starts on first run'}</CardTitle>
                  <Badge variant="outline"><HardDrive data-icon="inline-start" /> /workspace</Badge>
                </div>
                <CardDescription className="truncate" title={sandboxId ?? undefined}>
                  {sandboxId ? `Daytona · ${sandboxId}` : 'Daytona isolated sandbox · persistent volume'}
                </CardDescription>
              </CardHeader>
            </Card>

            <Empty
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
              className={cn('gap-3 border border-dashed p-3', dragging && 'border-ring')}
            >
              <EmptyHeader>
                <EmptyMedia variant="icon"><Upload /></EmptyMedia>
                <EmptyDescription>Drop files to upload to /workspace.</EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!session || uploading}
                  onClick={() => fileInputRef.current?.click()}
                >
                  {uploading ? 'Uploading…' : 'Upload files'}
                </Button>
              </EmptyContent>
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
            </Empty>

            {fileError && <Alert variant="destructive"><AlertDescription>{fileError}</AlertDescription></Alert>}
          </div>

          <ScrollArea className="min-h-0 flex-1">
            <div className="px-3 pb-3">
              {files.length ? (
                <FileTree files={files} download={onDownload} />
              ) : (
                <Empty className="border border-dashed p-4">
                  <EmptyHeader>
                    <EmptyMedia variant="icon"><FolderOpen /></EmptyMedia>
                    <EmptyDescription role={filesLoading ? 'status' : undefined}>
                      {filesLoading ? 'Loading files…' : 'No workspace files yet.'}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
              )}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="memories" className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex shrink-0 items-center justify-between gap-1.5 px-3 pt-3 pb-2">
            <ToggleGroup
              aria-label="Filter memories"
              size="sm"
              variant="outline"
              spacing={1}
              className="flex-wrap"
              value={[memoryFilter]}
              onValueChange={([value]) => { if (value) setMemoryFilter(value as MemoryFilter) }}
            >
              <ToggleGroupItem value="all">All</ToggleGroupItem>
              <ToggleGroupItem value="session" disabled={!session}>This session</ToggleGroupItem>
              <ToggleGroupItem value="pending">
                {pendingCount ? `Pending (${pendingCount})` : 'Pending'}
              </ToggleGroupItem>
            </ToggleGroup>
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
                <Alert variant="destructive"><AlertDescription>{memoryError}</AlertDescription></Alert>
              ) : memories === null ? (
                <p role="status" className="px-1 py-4 text-center text-xs text-muted-foreground">
                  Loading memories…
                </p>
              ) : !filteredMemories.length ? (
                <Empty className="border border-dashed p-4">
                  <EmptyHeader>
                    <EmptyMedia variant="icon"><Brain /></EmptyMedia>
                    <EmptyDescription>
                      {memoryFilter === 'pending'
                        ? 'No pending memory candidates.'
                        : memoryFilter === 'session'
                          ? 'No memories saved for this session.'
                          : 'No memories yet.'}
                    </EmptyDescription>
                  </EmptyHeader>
                </Empty>
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
    </ResponsivePanel>
  )
}
