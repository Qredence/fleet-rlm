import { useChat } from '@ai-sdk/react'
import { DefaultChatTransport } from 'ai'
import { ArrowUp, KeyRound, PanelLeft, Square, Terminal } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { MemoryDrawer } from '@/components/agent/MemoryDrawer'
import { MessageParts } from '@/components/agent/MessageParts'
import { SkillPicker } from '@/components/agent/SkillPicker'
import { WorkspaceSidebar, type WorkspaceSidebarProps } from '@/components/agent/WorkspaceSidebar'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { bearerHeaders, chatPath, createApi, errorMessage, type FleetApi, type Session, type Skill, type WorkspaceFile } from '@/lib/api'
import { chatFetch, STOP_RETRY_WINDOW_MS } from '@/lib/chat-fetch'
import type { FleetMessage } from '@/lib/messages'

const tokenKey = 'fleet-rlm.bearer-token'
const sessionKey = 'fleet-rlm.session'

type StopTimes = RefObject<Map<string, number>>

function TokenSettings({ token, onSave }: { token: string; onSave: (value: string) => void }) {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  return (
    <Dialog open={open} onOpenChange={(next) => {
      if (next) setValue(token)
      setOpen(next)
    }}>
      <DialogTrigger render={<Button size="icon-sm" variant="ghost" aria-label="API access" />}><KeyRound /></DialogTrigger>
      <DialogContent>
        <form className="grid gap-4" onSubmit={(event) => {
          event.preventDefault()
          onSave(value.trim())
          setOpen(false)
        }}>
          <DialogHeader>
            <DialogTitle>API access</DialogTitle>
            <DialogDescription>Enter a bearer token if your Fleet server requires one.</DialogDescription>
          </DialogHeader>
          <label className="grid gap-1.5 text-sm font-medium">
            Bearer token
            <input type="password" autoComplete="off" className="field" value={value} onChange={(event) => setValue(event.target.value)} />
          </label>
          <DialogFooter><Button type="submit">Save</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function Chat({ session, token, initialMessages, skills, recentStops, download, onComplete }: {
  session: Session
  token: string
  initialMessages: FleetMessage[]
  skills: Skill[]
  recentStops: StopTimes
  download: (path: string) => void
  onComplete: () => void
}) {
  const [prompt, setPrompt] = useState('')
  const [pinned, setPinned] = useState<string[]>([])
  const [waitingForStop, setWaitingForStop] = useState(false)
  const activeRequest = useRef(false)
  const viewport = useRef<HTMLDivElement>(null)
  const followMessages = useRef(true)
  const consumeRecentStop = useCallback(() => {
    const stoppedAt = recentStops.current.get(session.id)
    recentStops.current.delete(session.id)
    return stoppedAt !== undefined && Date.now() - stoppedAt < STOP_RETRY_WINDOW_MS
  }, [recentStops, session.id])
  const { messages, sendMessage, stop, status, error } = useChat<FleetMessage>({
    id: session.id,
    messages: initialMessages,
    transport: new DefaultChatTransport<FleetMessage>({
      api: chatPath(session.id),
      headers: bearerHeaders(token),
      prepareSendMessagesRequest: ({ messages: outgoing, body }) => {
        const latest = outgoing.findLast((message) => message.role === 'user')
        return {
          body: {
            messages: latest ? [{ role: latest.role, parts: latest.parts }] : [],
            pinned_skills: body?.pinned_skills ?? [],
          },
        }
      },
      fetch: chatFetch(consumeRecentStop, setWaitingForStop),
    }),
    onFinish: ({ isAbort, isDisconnect, isError }) => {
      if (!isAbort && !isDisconnect && !isError) onComplete()
    },
  })
  const active = status === 'submitted' || status === 'streaming'

  const stopChat = useCallback(() => {
    recentStops.current.set(session.id, Date.now())
    void stop()
  }, [recentStops, session.id, stop])

  useEffect(() => () => {
    if (activeRequest.current) stopChat()
  }, [stopChat])

  useLayoutEffect(() => {
    const element = viewport.current
    if (element && followMessages.current) element.scrollTop = element.scrollHeight
  }, [messages, status])

  async function submit() {
    const text = prompt.trim()
    if (!text || active || activeRequest.current) return
    followMessages.current = true
    setPrompt('')
    activeRequest.current = true
    try {
      await sendMessage({ text }, { body: { pinned_skills: pinned } })
    } finally {
      activeRequest.current = false
    }
  }

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Chat">
      <div ref={viewport} className="flex-1 overflow-y-auto" onScroll={(event) => {
        const { scrollTop, scrollHeight, clientHeight } = event.currentTarget
        followMessages.current = scrollHeight - scrollTop - clientHeight < 56
      }}>
        {messages.length ? (
          <div className="mx-auto grid w-full max-w-3xl gap-7 px-4 py-7 sm:px-8" aria-label="Conversation">
            {messages.map((message, index) => {
              const user = message.role === 'user'
              return (
                <article key={message.id} aria-label={user ? 'You' : 'Fleet'} className={`min-w-0 ${user ? 'ml-auto max-w-[90%] rounded-2xl rounded-br-md border border-primary/10 bg-primary/7 px-4 py-3 sm:max-w-[80%]' : 'w-full'}`}>
                  {!user && <div className="mb-2 flex items-center gap-2 text-sm font-semibold"><Terminal className="size-4 text-primary" /> Fleet</div>}
                  <MessageParts parts={message.parts} live={!user && active && index === messages.length - 1} download={download} />
                </article>
              )
            })}
          </div>
        ) : (
          <div className="flex h-full min-h-32 flex-col items-center justify-center gap-3 px-5 text-center">
            <div className="rounded-xl border border-primary/10 bg-primary/7 p-3 text-primary"><Terminal className="size-6" /></div>
            <h2 className="text-2xl font-semibold tracking-tight">Start a conversation</h2>
            <p className="max-w-sm text-sm text-muted-foreground">Ask Fleet a question or upload a file to work with.</p>
          </div>
        )}
      </div>
      <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pb-4 sm:px-8 sm:pb-6">
        {error && <p role="alert" className="mb-2 rounded-md border border-destructive/25 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error.message}</p>}
        {active && <p role="status" className="mb-2 px-1 text-xs text-muted-foreground">{waitingForStop ? 'Stopping previous run...' : status === 'submitted' ? 'Starting run…' : 'Run in progress…'}</p>}
        <form className="grid gap-2 rounded-xl border bg-card p-2 shadow-xs focus-within:border-primary/40" onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}>
          <Textarea
            className="min-h-16 max-h-40 resize-none border-0 bg-transparent px-2 py-2 focus-visible:ring-0"
            aria-label="Message Fleet"
            placeholder="Message Fleet…"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                void submit()
              }
            }}
          />
          <div className="flex min-w-0 items-end justify-between gap-2 px-1 pb-0.5">
            <SkillPicker skills={skills} selected={pinned} onChange={setPinned} />
            <div className="ml-auto shrink-0">
              {active ? <Button size="sm" variant="outline" onClick={stopChat}><Square /> Stop</Button> : (
                <Button type="submit" size="sm" disabled={!prompt.trim()}><ArrowUp /> Send</Button>
              )}
            </div>
          </div>
        </form>
      </div>
    </section>
  )
}

type SessionControls = Pick<WorkspaceSidebarProps, 'sessions' | 'sessionsLoading' | 'changingSessions' | 'onCreate' | 'onDelete' | 'onSelect'>

function SessionWorkspace({ api, token, session, skills, recentStops, controls, sidebarOpen, workspaceError }: {
  api: FleetApi
  token: string
  session?: Session
  skills: Skill[]
  recentStops: StopTimes
  controls: SessionControls
  sidebarOpen: boolean
  workspaceError: string
}) {
  const [messages, setMessages] = useState<FleetMessage[] | null>(null)
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null)
  const [chatError, setChatError] = useState('')
  const [fileError, setFileError] = useState('')
  const [uploading, setUploading] = useState(false)
  const fileRequest = useRef<AbortController | null>(null)
  const sessionId = session?.id

  useEffect(() => {
    if (!sessionId) return
    const request = new AbortController()
    void api.getMessages(sessionId, request.signal).then(setMessages).catch((failure) => {
      if (!request.signal.aborted) setChatError(errorMessage(failure))
    })
    return () => request.abort()
  }, [api, sessionId])

  const refreshFiles = useCallback(() => {
    if (!sessionId) return
    fileRequest.current?.abort()
    const request = new AbortController()
    fileRequest.current = request
    void api.listFiles(sessionId, request.signal).then((result) => {
      if (!request.signal.aborted) setFiles(result)
    }).catch((failure) => {
      if (!request.signal.aborted) {
        setFiles((current) => current ?? [])
        setFileError(errorMessage(failure))
      }
    })
  }, [api, sessionId])

  useEffect(() => {
    refreshFiles()
    return () => fileRequest.current?.abort()
  }, [refreshFiles])

  async function upload(file: File) {
    if (!sessionId) return
    setUploading(true)
    setFileError('')
    try {
      const record = await api.uploadFile(sessionId, file)
      setFiles((current) => [...(current ?? []).filter((item) => item.path !== record.path), record])
      if (!fileRequest.current?.signal.aborted) refreshFiles()
    } catch (failure) {
      setFileError(errorMessage(failure))
    } finally {
      setUploading(false)
    }
  }

  function download(path: string) {
    if (!sessionId) return
    setFileError('')
    void api.downloadFile(sessionId, path).catch((failure) => setFileError(errorMessage(failure)))
  }

  return (
    <div className="relative flex min-h-0 flex-1">
      <div className={`${sidebarOpen ? 'flex' : 'hidden'} absolute inset-y-0 left-0 z-10 w-64 border-r shadow-lg md:static md:flex md:shrink-0 md:shadow-none`}>
        <WorkspaceSidebar {...controls} session={session} files={files ?? []} filesLoading={Boolean(sessionId && !files)} uploading={uploading} error={workspaceError || fileError} onDownload={download} onUpload={(file) => void upload(file)} />
      </div>
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-12 shrink-0 items-center border-b px-5 sm:px-8">
          <h1 className="min-w-0 truncate text-sm font-semibold">{session?.title ?? 'Workspace'}</h1>
        </div>
        {session && messages ? (
          <Chat session={session} token={token} initialMessages={messages} skills={skills} recentStops={recentStops} download={download} onComplete={refreshFiles} />
        ) : (
          <div className="grid flex-1 place-items-center px-5 text-center">
            {workspaceError || chatError ? <p role="alert" className="text-sm text-destructive">{workspaceError || chatError}</p> : (
              session || controls.sessionsLoading ? <p role="status" className="text-sm text-muted-foreground">Loading workspace…</p> : (
                <Button variant="outline" disabled={controls.changingSessions} onClick={() => void controls.onCreate()}>Create a session</Button>
              )
            )}
          </div>
        )}
        {fileError && !sidebarOpen && <p role="alert" className="px-5 pb-3 text-xs text-destructive md:hidden">{fileError}</p>}
      </main>
    </div>
  )
}

function Workspace({ token, onTokenChange }: { token: string; onTokenChange: (value: string) => void }) {
  const api = useMemo(() => createApi(token), [token])
  const [sessions, setSessions] = useState<Session[]>([])
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(sessionKey) ?? '')
  const [skills, setSkills] = useState<Skill[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [changingSessions, setChangingSessions] = useState(false)
  const [workspaceError, setWorkspaceError] = useState('')
  const [skillsError, setSkillsError] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const recentStops = useRef(new Map<string, number>())
  const session = sessions.find((item) => item.id === sessionId)

  const selectSession = useCallback((id: string) => {
    setSessionId(id)
    if (id) localStorage.setItem(sessionKey, id)
    else localStorage.removeItem(sessionKey)
  }, [])

  useEffect(() => {
    const request = new AbortController()
    void api.listSessions(request.signal).then((items) => {
      if (request.signal.aborted) return
      setSessions(items)
      const saved = localStorage.getItem(sessionKey)
      selectSession(items.find((item) => item.id === saved)?.id ?? items[0]?.id ?? '')
    }).catch((failure) => {
      if (!request.signal.aborted) setWorkspaceError(errorMessage(failure))
    }).finally(() => {
      if (!request.signal.aborted) setSessionsLoading(false)
    })
    void api.listSkills(request.signal).then(setSkills).catch((failure) => {
      if (!request.signal.aborted) setSkillsError(`Skills: ${errorMessage(failure)}`)
    })
    return () => request.abort()
  }, [api, selectSession])

  async function createSession(title?: string) {
    setChangingSessions(true)
    setWorkspaceError('')
    try {
      const created = await api.createSession(title)
      setSessions((current) => [created, ...current])
      selectSession(created.id)
      setSidebarOpen(false)
      return true
    } catch (failure) {
      setWorkspaceError(errorMessage(failure))
      return false
    } finally {
      setChangingSessions(false)
    }
  }

  async function deleteSession(id: string) {
    setChangingSessions(true)
    setWorkspaceError('')
    try {
      await api.deleteSession(id)
      recentStops.current.delete(id)
      const remaining = sessions.filter((item) => item.id !== id)
      setSessions(remaining)
      selectSession(remaining[0]?.id ?? '')
      return true
    } catch (failure) {
      setWorkspaceError(errorMessage(failure))
      return false
    } finally {
      setChangingSessions(false)
    }
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b bg-card px-4 sm:px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <Button size="icon-sm" variant="ghost" className="md:hidden" aria-label="Toggle workspace sidebar" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(!sidebarOpen)}><PanelLeft /></Button>
          <div className="rounded-lg bg-primary p-1.5 text-primary-foreground"><Terminal className="size-4" /></div>
          <span className="truncate text-sm font-semibold tracking-tight">Fleet RLM</span>
        </div>
        <div className="flex items-center gap-1">
          <MemoryDrawer api={api} sessionId={session?.id} />
          <TokenSettings token={token} onSave={onTokenChange} />
        </div>
      </header>
      {skillsError && !workspaceError && <p role="alert" className="border-b px-4 py-1 text-xs text-destructive">{skillsError}</p>}
      <SessionWorkspace key={session?.id ?? 'empty'} api={api} token={token} session={session} skills={skills} recentStops={recentStops} sidebarOpen={sidebarOpen} workspaceError={workspaceError} controls={{
        sessions, sessionsLoading, changingSessions,
        onCreate: createSession,
        onDelete: deleteSession,
        onSelect: (id) => { setWorkspaceError(''); selectSession(id); setSidebarOpen(false) },
      }} />
    </div>
  )
}

export default function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(tokenKey) ?? '')
  return <Workspace key={token} token={token} onTokenChange={(value) => {
    if (value) sessionStorage.setItem(tokenKey, value)
    else sessionStorage.removeItem(tokenKey)
    setToken(value)
  }} />
}
