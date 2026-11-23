import { useChat } from '@ai-sdk/react'
import { DefaultChatTransport } from 'ai'
import { Moon, PanelLeft, PanelRight, Settings, Sun, Terminal } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { ChatComposer } from '@/components/agent/ChatComposer'
import { FilePreview } from '@/components/agent/FilePreview'
import { MessageParts } from '@/components/agent/MessageParts'
import type { PanelPresentation } from '@/components/agent/ResponsivePanel'
import { SessionsSidebar } from '@/components/agent/SessionsSidebar'
import { SettingsDialog } from '@/components/agent/SettingsDialog'
import { WorkspaceSidePanel } from '@/components/agent/WorkspaceSidePanel'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Bubble, BubbleContent } from '@/components/ui/bubble'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Message, MessageContent, MessageHeader } from '@/components/ui/message'
import {
  MessageScroller, MessageScrollerButton, MessageScrollerContent, MessageScrollerItem,
  MessageScrollerProvider, MessageScrollerViewport,
} from '@/components/ui/message-scroller'
import { Separator } from '@/components/ui/separator'
import { useMediaQuery } from '@/hooks/use-media-query'
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
  preview,
  download,
  onComplete,
}: {
  session: Session
  token: string
  initialMessages: FleetMessage[]
  skills: Skill[]
  recentStops: StopTimes
  preview: (path: string) => void
  download: (path: string) => void
  onComplete: () => void
}) {
  const [prompt, setPrompt] = useState('')
  const [pinned, setPinned] = useState<string[]>([])
  const [waitingForStop, setWaitingForStop] = useState(false)
  const activeRequest = useRef(false)

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
  const latestPromptIndex = messages.findLastIndex((message) => message.role === 'user')

  const stopChat = useCallback(() => {
    recentStops.current.set(session.id, Date.now())
    void stop()
  }, [recentStops, session.id, stop])

  useEffect(() => () => {
    if (activeRequest.current) stopChat()
  }, [stopChat])

  async function submit(text: string) {
    if (!text || active || activeRequest.current) return
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
      <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
        <MessageScroller className="flex-1">
          <MessageScrollerViewport>
            <MessageScrollerContent className="mx-auto w-full max-w-3xl px-4 py-7 sm:px-8" aria-label="Conversation">
              {messages.length ? messages.map((message, index) => {
                const user = message.role === 'user'
                return (
                  <MessageScrollerItem
                    key={message.id}
                    messageId={message.id}
                    scrollAnchor={user}
                    // Measure the latest turn at its real height before positioning the saved transcript.
                    className={latestPromptIndex >= 0 && index >= latestPromptIndex ? '[content-visibility:visible]' : undefined}
                  >
                    <Message align={user ? 'end' : 'start'} role="article" aria-label={user ? 'You' : 'Fleet'}>
                      <MessageContent>
                        {user ? (
                          <Bubble variant="muted" align="end" className="max-w-[90%] sm:max-w-[80%]">
                            <BubbleContent>
                              <MessageParts parts={message.parts} preview={preview} download={download} />
                            </BubbleContent>
                          </Bubble>
                        ) : (
                          <>
                            <MessageHeader className="gap-2 px-0">
                              <Terminal className="size-4" aria-hidden="true" /> Fleet
                            </MessageHeader>
                            <MessageParts
                              parts={message.parts}
                              live={active && index === messages.length - 1}
                              preview={preview}
                              download={download}
                            />
                          </>
                        )}
                      </MessageContent>
                    </Message>
                  </MessageScrollerItem>
                )
              }) : (
                <MessageScrollerItem className="flex flex-1 items-center">
                  <Empty>
                    <EmptyHeader>
                      <EmptyMedia variant="icon"><Terminal /></EmptyMedia>
                      <EmptyTitle>Start a conversation</EmptyTitle>
                      <EmptyDescription>
                        Ask Fleet a question or drop files into the workspace panel.
                        {skills.length > 0 && ' Type @ or open Skills to pin a skill for your next run.'}
                      </EmptyDescription>
                    </EmptyHeader>
                  </Empty>
                </MessageScrollerItem>
              )}
            </MessageScrollerContent>
          </MessageScrollerViewport>
          <MessageScrollerButton aria-label="Jump to latest message" />
        </MessageScroller>
      </MessageScrollerProvider>

      <div className="mx-auto flex w-full max-w-3xl shrink-0 flex-col gap-2 px-4 pb-4 sm:px-8 sm:pb-6">
        {error && (
          <Alert variant="destructive"><AlertDescription>{error.message}</AlertDescription></Alert>
        )}
        {active && (
          <p role="status" className="px-1 text-xs text-muted-foreground">
            {waitingForStop ? 'Stopping previous run…' : status === 'submitted' ? 'Starting run…' : 'Run in progress…'}
          </p>
        )}
        <ChatComposer
          skills={skills}
          pinned={pinned}
          setPinned={setPinned}
          value={prompt}
          onValueChange={setPrompt}
          onSubmit={submit}
          onStop={stopChat}
          loading={active}
        />
      </div>
    </section>
  )
}

function SessionWorkspace({
  api,
  token,
  session,
  skills,
  recentStops,
  sessionsLoading,
  changingSessions,
  onCreateSession,
  workspacePanel,
  workspaceError,
  onRunComplete,
  onSkillInstalled,
}: {
  api: FleetApi
  token: string
  session?: Session
  skills: Skill[]
  recentStops: StopTimes
  sessionsLoading: boolean
  changingSessions: boolean
  onCreateSession: () => Promise<boolean>
  workspacePanel: PanelPresentation
  workspaceError: string
  onRunComplete: () => void
  onSkillInstalled: (skill: Skill) => void
}) {
  const [messages, setMessages] = useState<FleetMessage[] | null>(null)
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null)
  const [chatError, setChatError] = useState('')
  const [fileError, setFileError] = useState('')
  const [previewPath, setPreviewPath] = useState<string>()
  const [pendingUploads, setPendingUploads] = useState(0)
  const [memoryRefreshKey, setMemoryRefreshKey] = useState(0)
  const fileRequest = useRef<AbortController | null>(null)
  const sessionId = session?.id
  const uploading = pendingUploads > 0

  useEffect(() => {
    if (!sessionId) return
    const request = new AbortController()
    void api.getMessages(sessionId, request.signal)
      .then((items) => {
        if (!request.signal.aborted) {
          setMessages(items)
          setChatError('')
        }
      })
      .catch((failure) => {
        if (!request.signal.aborted) setChatError(errorMessage(failure))
      })
    return () => request.abort()
  }, [api, sessionId])

  const refreshFiles = useCallback((options?: { preserveError?: boolean }) => {
    if (!sessionId) return
    fileRequest.current?.abort()
    const request = new AbortController()
    fileRequest.current = request
    void api.listFiles(sessionId, request.signal)
      .then((result) => {
        if (!request.signal.aborted) {
          setFiles(result)
          if (!options?.preserveError) setFileError('')
        }
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

  async function uploadFiles(batch: File[]) {
    if (!sessionId || batch.length === 0) return
    setPendingUploads((count) => count + 1)
    setFileError('')
    let uploadedAny = false
    let needsSandboxRefresh = !session?.daytona_sandbox_id
    const errors: string[] = []
    try {
      for (const file of batch) {
        if (fileRequest.current?.signal.aborted) break
        try {
          const record = await api.uploadFile(sessionId, file)
          uploadedAny = true
          if (!fileRequest.current?.signal.aborted) {
            setFiles((current) => [...(current ?? []).filter((item) => item.path !== record.path), record])
          }
          if (needsSandboxRefresh) {
            needsSandboxRefresh = false
            onRunComplete()
          }
        } catch (failure) {
          const message = errorMessage(failure)
          errors.push(batch.length > 1 ? `${file.name}: ${message}` : message)
        }
      }
      if (uploadedAny && !fileRequest.current?.signal.aborted) {
        refreshFiles({ preserveError: errors.length > 0 })
      }
      if (errors.length && !fileRequest.current?.signal.aborted) {
        setFileError(errors.join(' · '))
      }
    } finally {
      setPendingUploads((count) => Math.max(0, count - 1))
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
    <>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        {session && messages ? (
          <Chat
            session={session}
            token={token}
            initialMessages={messages}
            skills={skills}
            recentStops={recentStops}
            preview={setPreviewPath}
            download={download}
            onComplete={handleChatComplete}
          />
        ) : (
          <div className="grid flex-1 place-items-center px-5 text-center">
            {workspaceError || chatError ? (
              <Alert variant="destructive">
                <AlertDescription>{workspaceError || chatError}</AlertDescription>
              </Alert>
            ) : session || sessionsLoading ? (
              <p role="status" className="text-sm text-muted-foreground">Loading workspace…</p>
            ) : (
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon"><Terminal /></EmptyMedia>
                  <EmptyTitle>Create your first session</EmptyTitle>
                  <EmptyDescription>Start a session to run Fleet RLM.</EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  <Button disabled={changingSessions} onClick={() => void onCreateSession()}>
                    Create a session
                  </Button>
                </EmptyContent>
              </Empty>
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
        presentation={workspacePanel}
        onUpload={(batch) => void uploadFiles(batch)}
        onPreview={setPreviewPath}
        onDownload={download}
      />
      {sessionId && previewPath && (
        <FilePreview
          key={previewPath}
          api={api}
          sessionId={sessionId}
          path={previewPath}
          onClose={() => setPreviewPath(undefined)}
          onDownload={() => api.downloadFile(sessionId, previewPath)}
          onSkillInstalled={onSkillInstalled}
        />
      )}
    </>
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
  const sessionsInline = useMediaQuery('(min-width: 48rem)')
  const workspaceInline = useMediaQuery('(min-width: 64rem)')
  const [sessionsRailOpen, setSessionsRailOpen] = useState(true)
  const [workspaceRailOpen, setWorkspaceRailOpen] = useState(true)
  const [mobilePanel, setMobilePanel] = useState<'sessions' | 'workspace' | null>(null)
  const sessionsTrigger = useRef<HTMLButtonElement>(null)
  const workspaceTrigger = useRef<HTMLButtonElement>(null)
  const sessionsOpen = sessionsInline ? sessionsRailOpen : mobilePanel === 'sessions'
  const workspaceOpen = workspaceInline ? workspaceRailOpen : mobilePanel === 'workspace'
  const [settingsOpen, setSettingsOpen] = useState(false)
  const recentStops = useRef(new Map<string, number>())
  const session = sessions.find((item) => item.id === sessionId)

  if ((sessionsInline && mobilePanel === 'sessions') || (workspaceInline && mobilePanel === 'workspace')) {
    setMobilePanel(null)
  }

  function closePanel(panel: 'sessions' | 'workspace') {
    setMobilePanel((current) => current === panel ? null : current)
  }

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

  function addInstalledSkill(skill: Skill) {
    setSkills((current) => [...current.filter((item) => item.name !== skill.name), skill]
      .sort((left, right) => left.name.localeCompare(right.name)))
  }

  useEffect(() => {
    const request = new AbortController()
    void api.listSessions(request.signal)
      .then((items) => {
        if (request.signal.aborted) return
        setWorkspaceError('')
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
      .then((items) => {
        if (!request.signal.aborted) {
          setSkillsError('')
          setSkills(items)
        }
      })
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
      closePanel('sessions')
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
            ref={sessionsTrigger}
            aria-label="Toggle sessions sidebar"
            aria-expanded={sessionsOpen}
            aria-haspopup={sessionsInline ? undefined : 'dialog'}
            onClick={() => {
              if (sessionsInline) setSessionsRailOpen((open) => !open)
              else setMobilePanel('sessions')
            }}
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
                    className="max-w-48 truncate"
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
            aria-label={resolvedTheme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            title={resolvedTheme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={toggleTheme}
          >
            {resolvedTheme === 'dark' ? <Sun /> : <Moon />}
          </Button>
          <Button
            type="button"
            size="icon-sm"
            variant={workspaceOpen ? 'secondary' : 'ghost'}
            ref={workspaceTrigger}
            aria-label="Toggle workspace panel"
            aria-expanded={workspaceOpen}
            aria-haspopup={workspaceInline ? undefined : 'dialog'}
            title="Toggle Files & Memories panel"
            onClick={() => {
              if (workspaceInline) setWorkspaceRailOpen((open) => !open)
              else setMobilePanel('workspace')
            }}
          >
            <PanelRight />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-label="Settings"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings data-icon="inline-start" />
            <span className="hidden sm:inline">Settings</span>
          </Button>
        </div>
      </header>

      {skillsError && !workspaceError && (
        <Alert variant="destructive"><AlertDescription>{skillsError}</AlertDescription></Alert>
      )}

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <SessionsSidebar
          session={session}
          sessions={sessions}
          sessionsLoading={sessionsLoading}
          changingSessions={changingSessions}
          error={workspaceError}
          presentation={{
            inline: sessionsInline, open: sessionsOpen,
            onClose: () => closePanel('sessions'), triggerRef: sessionsTrigger,
          }}
          onCreate={createSession}
          onRename={renameSession}
          onDelete={deleteSession}
          onSelect={(id) => {
            setWorkspaceError('')
            selectSession(id)
            closePanel('sessions')
          }}
        />
        <SessionWorkspace
          key={session?.id ?? 'empty'}
          api={api}
          token={token}
          session={session}
          skills={skills}
          recentStops={recentStops}
          sessionsLoading={sessionsLoading}
          changingSessions={changingSessions}
          onCreateSession={createSession}
          workspacePanel={{
            inline: workspaceInline, open: workspaceOpen,
            onClose: () => closePanel('workspace'), triggerRef: workspaceTrigger,
          }}
          workspaceError={workspaceError}
          onRunComplete={refreshSessions}
          onSkillInstalled={addInstalledSkill}
        />
      </div>

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        api={api}
        token={token}
        onTokenChange={(value) => {
          setSessionsLoading(true)
          setWorkspaceError('')
          setSkillsError('')
          onTokenChange(value)
        }}
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
      token={token}
      onTokenChange={(value) => {
        if (value) sessionStorage.setItem(tokenKey, value)
        else sessionStorage.removeItem(tokenKey)
        setToken(value)
      }}
    />
  )
}
