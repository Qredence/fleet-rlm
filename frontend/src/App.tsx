import { useChat } from '@ai-sdk/react'
import { DefaultChatTransport } from 'ai'
import { ArrowUp, Moon, PanelLeft, PanelRight, Settings, Square, Sun, Terminal } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { MessageParts } from '@/components/agent/MessageParts'
import { SessionsSidebar, type SessionsSidebarProps } from '@/components/agent/SessionsSidebar'
import { SettingsDialog } from '@/components/agent/SettingsDialog'
import { SkillPicker } from '@/components/agent/SkillPicker'
import { WorkspaceSidePanel } from '@/components/agent/WorkspaceSidePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { useTheme } from '@/hooks/use-theme'
import {
  bearerHeaders,
  chatPath,
  createApi,
  errorMessage,
  type FleetApi,
  type Session,
  type SessionUpdate,
  type Skill,
  type WorkspaceFile,
} from '@/lib/api'
import { chatFetch, STOP_RETRY_WINDOW_MS } from '@/lib/chat-fetch'
import type { FleetMessage } from '@/lib/messages'

const tokenKey = 'fleet-rlm.bearer-token'
const sessionKey = 'fleet-rlm.session'

type StopTimes = RefObject<Map<string, number>>

function Chat({
  session,
  token,
  initialMessages,
  skills,
  recentStops,
  download,
  onComplete,
}: {
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
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-background" aria-label="Chat">
      <div
        ref={viewport}
        className="flex-1 overflow-y-auto"
        onScroll={(event) => {
          const { scrollTop, scrollHeight, clientHeight } = event.currentTarget
          followMessages.current = scrollHeight - scrollTop - clientHeight < 56
        }}
      >
        {messages.length ? (
          <div className="mx-auto grid w-full max-w-3xl gap-7 px-4 py-7 sm:px-8" aria-label="Conversation">
            {messages.map((message, index) => {
              const user = message.role === 'user'
              return (
                <article
                  key={message.id}
                  aria-label={user ? 'You' : 'Fleet'}
                  className={`min-w-0 ${
                    user
                      ? 'ml-auto max-w-[90%] rounded-2xl rounded-br-md border border-border bg-secondary/75 px-4 py-3 text-foreground sm:max-w-[80%]'
                      : 'w-full'
                  }`}
                >
                  {!user && (
                    <div className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-tight text-foreground">
                      <span className="inline-flex size-5 items-center justify-center rounded-md border bg-card text-foreground">
                        <Terminal className="size-3" />
                      </span>
                      Fleet
                    </div>
                  )}
                  <MessageParts
                    parts={message.parts}
                    live={!user && active && index === messages.length - 1}
                    download={download}
                  />
                </article>
              )
            })}
          </div>
        ) : (
          <div className="flex h-full min-h-36 flex-col items-center justify-center gap-3 px-6 text-center">
            <div className="rounded-2xl border bg-card p-3.5 text-foreground shadow-xs">
              <Terminal className="size-5" />
            </div>
            <h2 className="text-xl font-semibold tracking-tight">Start a conversation</h2>
            <p className="max-w-sm text-sm text-muted-foreground">
              Ask Fleet a question, pin a skill with @, or drop files into the workspace panel.
            </p>
          </div>
        )}
      </div>

      <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pb-4 sm:px-8 sm:pb-6">
        {error && (
          <p
            role="alert"
            className="mb-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          >
            {error.message}
          </p>
        )}
        {active && (
          <p role="status" className="mb-2 px-1 text-xs text-muted-foreground">
            {waitingForStop ? 'Stopping previous run…' : status === 'submitted' ? 'Starting run…' : 'Run in progress…'}
          </p>
        )}
        <form
          className="grid gap-2 rounded-2xl border bg-card p-2.5 shadow-xs transition-colors focus-within:border-foreground/30"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <Textarea
            className="min-h-14 max-h-40 resize-none border-0 bg-transparent px-2 py-1.5 shadow-none focus-visible:ring-0 dark:bg-transparent"
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
              {active ? (
                <Button type="button" size="sm" variant="outline" className="rounded-xl" onClick={stopChat}>
                  <Square /> Stop
                </Button>
              ) : (
                <Button type="submit" size="sm" className="rounded-xl" disabled={!prompt.trim()}>
                  <ArrowUp /> Send
                </Button>
              )}
            </div>
          </div>
        </form>
      </div>
    </section>
  )
}

type SessionControls = Pick<
  SessionsSidebarProps,
  'sessions' | 'sessionsLoading' | 'changingSessions' | 'onCreate' | 'onRename' | 'onDelete' | 'onSelect'
>

function SessionWorkspace({
  api,
  token,
  session,
  skills,
  recentStops,
  controls,
  leftSidebarOpen,
  onCloseLeftSidebar,
  rightPanelOpen,
  onCloseRightPanel,
  workspaceError,
  onRunComplete,
}: {
  api: FleetApi
  token: string
  session?: Session
  skills: Skill[]
  recentStops: StopTimes
  controls: SessionControls
  leftSidebarOpen: boolean
  onCloseLeftSidebar: () => void
  rightPanelOpen: boolean
  onCloseRightPanel: () => void
  workspaceError: string
  onRunComplete: () => void
}) {
  const [messages, setMessages] = useState<FleetMessage[] | null>(null)
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null)
  const [chatError, setChatError] = useState('')
  const [fileError, setFileError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [memoryRefreshKey, setMemoryRefreshKey] = useState(0)
  const fileRequest = useRef<AbortController | null>(null)
  const sessionId = session?.id

  useEffect(() => {
    if (!sessionId) return
    const request = new AbortController()
    void api.getMessages(sessionId, request.signal)
      .then(setMessages)
      .catch((failure) => {
        if (!request.signal.aborted) setChatError(errorMessage(failure))
      })
    return () => request.abort()
  }, [api, sessionId])

  const refreshFiles = useCallback(() => {
    if (!sessionId) return
    fileRequest.current?.abort()
    const request = new AbortController()
    fileRequest.current = request
    void api.listFiles(sessionId, request.signal)
      .then((result) => {
        if (!request.signal.aborted) setFiles(result)
      })
      .catch((failure) => {
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

  const handleChatComplete = useCallback(() => {
    refreshFiles()
    setMemoryRefreshKey((count) => count + 1)
    onRunComplete()
  }, [refreshFiles, onRunComplete])

  return (
    <div className="relative flex min-h-0 flex-1 overflow-hidden">
      {leftSidebarOpen && (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-20 bg-black/25 backdrop-blur-[1px] md:hidden"
          onClick={onCloseLeftSidebar}
        />
      )}
      <div
        className={`${
          leftSidebarOpen ? 'flex' : 'hidden'
        } fixed inset-y-0 left-0 z-30 w-64 border-r shadow-xl md:static md:z-auto md:shrink-0 md:shadow-none`}
      >
        <SessionsSidebar {...controls} session={session} error={workspaceError} />
      </div>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {session && messages ? (
          <Chat
            session={session}
            token={token}
            initialMessages={messages}
            skills={skills}
            recentStops={recentStops}
            download={download}
            onComplete={handleChatComplete}
          />
        ) : (
          <div className="grid flex-1 place-items-center px-5 text-center">
            {workspaceError || chatError ? (
              <p role="alert" className="text-sm text-destructive">{workspaceError || chatError}</p>
            ) : session || controls.sessionsLoading ? (
              <p role="status" className="text-sm text-muted-foreground">Loading workspace…</p>
            ) : (
              <div className="grid justify-items-center gap-3">
                <div className="rounded-2xl border bg-card p-3.5 shadow-xs">
                  <Terminal className="size-5" />
                </div>
                <p className="text-sm text-muted-foreground">Create your first session to start running Fleet RLM.</p>
                <Button
                  variant="default"
                  className="rounded-xl"
                  disabled={controls.changingSessions}
                  onClick={() => void controls.onCreate()}
                >
                  Create a session
                </Button>
              </div>
            )}
          </div>
        )}
      </main>

      <WorkspaceSidePanel
        api={api}
        session={session}
        files={files ?? []}
        filesLoading={Boolean(sessionId && !files)}
        uploading={uploading}
        fileError={fileError}
        memoryRefreshKey={memoryRefreshKey}
        open={rightPanelOpen}
        onClose={onCloseRightPanel}
        onUpload={(file) => void upload(file)}
        onDownload={download}
      />
    </div>
  )
}

function Workspace({ token, onTokenChange }: { token: string; onTokenChange: (value: string) => void }) {
  const api = useMemo(() => createApi(token), [token])
  const { theme, resolvedTheme, systemTheme, setTheme, toggleTheme } = useTheme()
  const [sessions, setSessions] = useState<Session[]>([])
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(sessionKey) ?? '')
  const [skills, setSkills] = useState<Skill[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [changingSessions, setChangingSessions] = useState(false)
  const [workspaceError, setWorkspaceError] = useState('')
  const [skillsError, setSkillsError] = useState('')
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(() => window.matchMedia('(min-width: 768px)').matches)
  const [rightPanelOpen, setRightPanelOpen] = useState(() => window.matchMedia('(min-width: 1024px)').matches)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const recentStops = useRef(new Map<string, number>())
  const session = sessions.find((item) => item.id === sessionId)

  const selectSession = useCallback((id: string) => {
    setSessionId(id)
    if (id) localStorage.setItem(sessionKey, id)
    else localStorage.removeItem(sessionKey)
  }, [])

  const refreshSessions = useCallback(() => {
    void api.listSessions()
      .then((items) => setSessions(items))
      .catch(() => {
        // Ignore silent background session refreshes.
      })
  }, [api])

  useEffect(() => {
    const request = new AbortController()
    void api.listSessions(request.signal)
      .then((items) => {
        if (request.signal.aborted) return
        setSessions(items)
        const saved = localStorage.getItem(sessionKey)
        selectSession(items.find((item) => item.id === saved)?.id ?? items[0]?.id ?? '')
      })
      .catch((failure) => {
        if (!request.signal.aborted) setWorkspaceError(errorMessage(failure))
      })
      .finally(() => {
        if (!request.signal.aborted) setSessionsLoading(false)
      })
    void api.listSkills(request.signal)
      .then(setSkills)
      .catch((failure) => {
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
      if (!window.matchMedia('(min-width: 768px)').matches) setLeftSidebarOpen(false)
      return true
    } catch (failure) {
      setWorkspaceError(errorMessage(failure))
      return false
    } finally {
      setChangingSessions(false)
    }
  }

  async function updateSession(id: string, changes: SessionUpdate) {
    setChangingSessions(true)
    setWorkspaceError('')
    try {
      const updated = await api.updateSession(id, changes)
      setSessions((current) => current.map((item) => (item.id === id ? updated : item)))
      return true
    } catch (failure) {
      setWorkspaceError(errorMessage(failure))
      return false
    } finally {
      setChangingSessions(false)
    }
  }

  async function renameSession(id: string, title: string) {
    return updateSession(id, { title })
  }

  async function deleteSession(id: string) {
    setChangingSessions(true)
    setWorkspaceError('')
    try {
      await api.deleteSession(id)
      recentStops.current.delete(id)
      const remaining = sessions.filter((item) => item.id !== id)
      setSessions(remaining)
      if (sessionId === id) {
        selectSession(remaining[0]?.id ?? '')
      }
      return true
    } catch (failure) {
      setWorkspaceError(errorMessage(failure))
      return false
    } finally {
      setChangingSessions(false)
    }
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b bg-card/80 px-3 backdrop-blur-xs sm:px-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            className="rounded-xl"
            aria-label="Toggle sessions sidebar"
            aria-expanded={leftSidebarOpen}
            onClick={() => setLeftSidebarOpen((open) => !open)}
          >
            <PanelLeft />
          </Button>
          <div className="flex shrink-0 items-center gap-2">
            <div className="flex size-7 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <Terminal className="size-3.5" />
            </div>
            <span className="text-sm font-semibold tracking-tight">Fleet RLM</span>
          </div>
          {session && (
            <>
              <Separator orientation="vertical" className="hidden h-5 sm:block" />
              <div className="hidden min-w-0 items-center gap-2 sm:flex">
                <h1 className="min-w-0 truncate text-sm font-medium text-foreground" title={session.title}>
                  {session.title}
                </h1>
                {session.model_override && (
                  <Badge
                    variant="outline"
                    className="max-w-48 truncate rounded-full font-mono text-[11px] font-normal"
                    title={`Session model override: ${session.model_override}`}
                  >
                    {session.model_override}
                  </Badge>
                )}
              </div>
            </>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            className="rounded-xl"
            aria-label={resolvedTheme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            title={resolvedTheme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={toggleTheme}
          >
            {resolvedTheme === 'dark' ? <Sun /> : <Moon />}
          </Button>
          <Button
            type="button"
            size="icon-sm"
            variant={rightPanelOpen ? 'secondary' : 'ghost'}
            className="rounded-xl"
            aria-label="Toggle workspace panel"
            aria-expanded={rightPanelOpen}
            title="Toggle Files & Memories panel"
            onClick={() => setRightPanelOpen((open) => !open)}
          >
            <PanelRight />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="gap-1.5 rounded-xl"
            aria-label="Settings"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings />
            <span className="hidden sm:inline">Settings</span>
          </Button>
        </div>
      </header>

      {skillsError && !workspaceError && (
        <p role="alert" className="border-b px-4 py-1.5 text-xs text-destructive">
          {skillsError}
        </p>
      )}

      <SessionWorkspace
        key={session?.id ?? 'empty'}
        api={api}
        token={token}
        session={session}
        skills={skills}
        recentStops={recentStops}
        leftSidebarOpen={leftSidebarOpen}
        onCloseLeftSidebar={() => setLeftSidebarOpen(false)}
        rightPanelOpen={rightPanelOpen}
        onCloseRightPanel={() => setRightPanelOpen(false)}
        workspaceError={workspaceError}
        onRunComplete={refreshSessions}
        controls={{
          sessions,
          sessionsLoading,
          changingSessions,
          onCreate: createSession,
          onRename: renameSession,
          onDelete: deleteSession,
          onSelect: (id) => {
            setWorkspaceError('')
            selectSession(id)
            if (!window.matchMedia('(min-width: 768px)').matches) setLeftSidebarOpen(false)
          },
        }}
      />

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        api={api}
        token={token}
        onTokenChange={onTokenChange}
        theme={theme}
        systemTheme={systemTheme}
        onThemeChange={setTheme}
        session={session}
        onUpdateSession={updateSession}
      />
    </div>
  )
}

export default function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(tokenKey) ?? '')
  return (
    <Workspace
      key={token}
      token={token}
      onTokenChange={(value) => {
        if (value) sessionStorage.setItem(tokenKey, value)
        else sessionStorage.removeItem(tokenKey)
        setToken(value)
      }}
    />
  )
}
