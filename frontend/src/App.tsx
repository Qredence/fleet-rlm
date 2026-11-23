import { lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router'
import { AppSidebar } from '@/components/AppSidebar'
import { Deferred, DeferredOverlay } from '@/components/Deferred'
import { PageHeader } from '@/components/PageHeader'
import { ChatPlaceholder } from '@/components/agent/ChatPlaceholder'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import { useSessions } from '@/hooks/use-sessions'
import { useTheme } from '@/hooks/use-theme'
import { createApi, errorMessage, type SessionUpdate, type Skill } from '@/lib/api'
import { cn } from '@/lib/utils'

const tokenKey = 'fleet-rlm.bearer-token'
const loadChat = () => import('@/components/agent/SessionWorkspace')
const SessionWorkspace = lazy(() => loadChat().then(({ SessionWorkspace }) => ({ default: SessionWorkspace })))
const CapabilitiesPage = lazy(() => import('@/pages/CapabilitiesPage').then(({ CapabilitiesPage }) => ({ default: CapabilitiesPage })))
const MemoriesPage = lazy(() => import('@/pages/MemoriesPage').then(({ MemoriesPage }) => ({ default: MemoriesPage })))
const OptimizationPage = lazy(() => import('@/pages/OptimizationPage').then(({ OptimizationPage }) => ({ default: OptimizationPage })))
const SettingsDialog = lazy(() => import('@/components/agent/SettingsDialog').then(({ SettingsDialog }) => ({ default: SettingsDialog })))

function PageLoading({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} />
      <div role="status" aria-label={`Loading ${title.toLowerCase()}`} className="mx-auto grid w-full max-w-4xl gap-3 p-4 sm:p-7">
        <Skeleton className="h-8 w-48 motion-reduce:animate-none" />
        <Skeleton className="h-24 w-full motion-reduce:animate-none" />
      </div>
    </>
  )
}

function Workspace({ token, onTokenChange }: { token: string; onTokenChange: (value: string) => void }) {
  const api = useMemo(() => createApi(token), [token])
  const { theme, systemTheme, setTheme } = useTheme()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const chatRoute = useMatch('/sessions/:id')
  const prepareChat = Boolean(chatRoute) || pathname === '/'
  const routeSessionId = chatRoute?.params.id?.toLowerCase()
  const sessions = useSessions(api, routeSessionId)
  const { session, refresh: refreshSessions } = sessions
  const [running, setRunning] = useState<string | null>(null)
  const [completedAt, setCompletedAt] = useState(0)
  const [hasOpenedChat, setHasOpenedChat] = useState(false)
  const [skills, setSkills] = useState<Skill[] | null>(null)
  const [skillsError, setSkillsError] = useState('')
  const [actionError, setActionError] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [filesDocked, setFilesDocked] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(() => !document.cookie.split('; ').includes('sidebar_state=false'))
  const skillsRequest = useRef<AbortController | null>(null)
  const recentStops = useRef(new Map<string, number>())

  const refreshSkills = useCallback(() => {
    skillsRequest.current?.abort()
    const request = new AbortController()
    skillsRequest.current = request
    void api.listSkills(request.signal).then((items) => {
      if (request.signal.aborted) return
      setSkills(items)
      setSkillsError('')
    }).catch((failure) => {
      if (!request.signal.aborted) setSkillsError(errorMessage(failure))
    })
  }, [api])

  useEffect(() => {
    refreshSkills()
    return () => skillsRequest.current?.abort()
  }, [refreshSkills])

  useEffect(() => {
    // Start loading chat alongside the session request on chat entry points.
    if (prepareChat) void loadChat().catch(() => {})
  }, [prepareChat])

  if (chatRoute && session && !hasOpenedChat) setHasOpenedChat(true)

  const onTurnComplete = useCallback(() => {
    setCompletedAt(Date.now())
    refreshSessions()
  }, [refreshSessions])

  async function createSession() {
    setActionError('')
    try {
      const created = await sessions.create()
      void navigate(`/sessions/${created.id}`)
    } catch (failure) {
      setActionError(errorMessage(failure))
    }
  }

  async function deleteSession(id: string) {
    await sessions.remove(id)
    recentStops.current.delete(id)
    if (routeSessionId === id) void navigate('/', { replace: true })
  }

  async function updateSessionSettings(id: string, changes: SessionUpdate) {
    try {
      await sessions.update(id, changes)
      return true
    } catch (failure) {
      setActionError(errorMessage(failure))
      return false
    }
  }

  function addInstalledSkill(skill: Skill) {
    setSkills((items) => [...(items ?? []).filter(({ name }) => name !== skill.name), skill]
      .sort((left, right) => left.name.localeCompare(right.name)))
    refreshSkills()
  }

  const visibleSessions = session && !sessions.sessions.some(({ id }) => id === session.id)
    ? [session, ...sessions.sessions]
    : sessions.sessions

  return (
    <SidebarProvider
      className="h-dvh min-h-0 overflow-hidden"
      open={sidebarOpen}
      onOpenChange={(open) => {
        if (!open && document.activeElement?.closest('[data-sidebar="sidebar"]')) {
          document.getElementById('fleet-sidebar-trigger')?.focus()
        }
        setSidebarOpen(open)
      }}
    >
      <AppSidebar
        sessions={visibleSessions} loading={sessions.listLoading} changing={sessions.changing}
        error={actionError || sessions.listError} running={running} theme={theme} onThemeChange={setTheme}
        onCreate={() => void createSession()}
        onRename={(id, title) => sessions.update(id, { title })}
        onDelete={deleteSession}
        onSettings={() => { setActionError(''); setSettingsOpen(true) }}
      />
      <SidebarInset className={cn(
        'min-h-0 min-w-0 overflow-hidden',
        chatRoute && session && filesDocked && 'xl:bg-transparent xl:shadow-none xl:peer-data-[variant=inset]:my-0 xl:peer-data-[variant=inset]:rounded-none',
      )}>
        {session && (chatRoute || hasOpenedChat) && (
          <Deferred key={session.id} fallback={chatRoute ? <PageLoading title={session.title} /> : null}>
            <SessionWorkspace
              api={api} token={token} session={session} skills={skills ?? []}
              skillsError={skillsError ? `Skills: ${skillsError}` : ''}
              visible={Boolean(chatRoute)} recentStops={recentStops}
              filesDocked={filesDocked} setFilesDocked={setFilesDocked}
              onActivity={setRunning} onTurnComplete={onTurnComplete}
              onFilesUploaded={refreshSessions} onSkillInstalled={addInstalledSkill}
              onModelChange={(model) => sessions.update(session.id, { model_override: model })}
            />
          </Deferred>
        )}

        <Routes>
          <Route path="/" element={session ? (
            <Navigate to={`/sessions/${session.id}`} replace />
          ) : (
            <ChatPlaceholder
              loading={sessions.loading} error={sessions.error || actionError}
              actionLabel={sessions.error ? 'Retry' : 'New session'} disabled={sessions.changing}
              onAction={sessions.error ? sessions.retry : () => void createSession()}
            />
          )} />
          <Route path="/sessions/:id" element={session ? null : (
            <ChatPlaceholder
              loading={sessions.loading} error={sessions.error}
              actionLabel={sessions.missing ? 'Back to chat' : 'Retry'}
              onAction={sessions.missing ? () => void navigate('/', { replace: true }) : sessions.retry}
            />
          )} />
          <Route path="/memories" element={(
            <Deferred key="memories" fallback={<PageLoading title="Memories" />}>
              <MemoriesPage api={api} sessions={visibleSessions} completedAt={completedAt} />
            </Deferred>
          )} />
          <Route path="/capabilities/:name?" element={(
            <Deferred key="capabilities" fallback={<PageLoading title="Capabilities" />}>
              <CapabilitiesPage
                api={api} skills={skills ?? []} loading={!skills && !skillsError} error={skillsError}
                onRetry={() => { setSkillsError(''); refreshSkills() }}
              />
            </Deferred>
          )} />
          <Route path="/optimization" element={(
            <Deferred key="optimization" fallback={<PageLoading title="Optimization" />}>
              <OptimizationPage api={api} />
            </Deferred>
          )} />
          <Route path="*" element={(
            <ChatPlaceholder error="This page could not be found." actionLabel="Back to chat" onAction={() => void navigate('/', { replace: true })} />
          )} />
        </Routes>
      </SidebarInset>
      <DeferredOverlay open={settingsOpen}>
        <SettingsDialog
          open={settingsOpen} onOpenChange={setSettingsOpen}
          api={api} token={token} onTokenChange={onTokenChange}
          theme={theme} systemTheme={systemTheme} onThemeChange={setTheme}
          session={session} onUpdateSession={updateSessionSettings}
        />
      </DeferredOverlay>
    </SidebarProvider>
  )
}

export default function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(tokenKey) ?? '')
  return (
    <Workspace
      key={token} token={token}
      onTokenChange={(value) => {
        if (value) sessionStorage.setItem(tokenKey, value)
        else sessionStorage.removeItem(tokenKey)
        setToken(value)
      }}
    />
  )
}
