import {
  Brain, ChevronDown, Ellipsis, Gauge, MessageSquare, Monitor, Moon, Package, Pencil, Plus, Search,
  Settings, Sun, Trash2,
} from 'lucide-react'
import { lazy, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router'
import { DeferredOverlay } from '@/components/Deferred'
import { FluidHoverHighlight } from '@/components/fluid-hover-highlight'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { DeferredDropdownMenu } from '@/components/ui/deferred-dropdown-menu'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel,
  SidebarHeader, SidebarMenu, SidebarMenuAction, SidebarMenuButton, SidebarMenuItem, SidebarMenuSkeleton,
  SidebarSeparator, SidebarTrigger, useSidebar,
} from '@/components/ui/sidebar'
import { useFluidHover, useRegisterFluidHoverItem, type UseFluidHoverReturn } from '@/hooks/use-fluid-hover'
import type { ThemePreference } from '@/hooks/use-theme'
import { errorMessage, type Session } from '@/lib/api'

const SessionActionDialogs = lazy(() => import('./SessionActionDialogs').then((module) => ({ default: module.SessionActionDialogs })))
const sessionBatchSize = 10

const navigation = [
  { to: '/memories', label: 'Memories', icon: Brain },
  { to: '/capabilities', label: 'Capabilities', icon: Package },
  { to: '/optimization', label: 'Optimization', icon: Gauge },
]
const themeChoices = [
  { value: 'system', label: 'System', icon: Monitor },
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
] as const

function FluidMenu({ label, children }: {
  label: string
  children: (register: UseFluidHoverReturn['registerItem']) => ReactNode
}) {
  const ref = useRef<HTMLUListElement>(null)
  const hover = useFluidHover(ref, {
    axis: 'y',
    isItemDisabled: (row) => Boolean(row.querySelector('[data-sidebar="menu-button"]:disabled')),
  })
  return (
    <SidebarMenu ref={ref} {...hover.handlers} aria-label={label} className="relative isolate gap-0">
      <li role="presentation" aria-hidden="true" className="contents">
        <FluidHoverHighlight hover={hover} className="rounded-md" />
      </li>
      {children(hover.registerItem)}
    </SidebarMenu>
  )
}

function FluidRow({ index, register, children }: {
  index: number
  register: UseFluidHoverReturn['registerItem']
  children: ReactNode
}) {
  const ref = useRef<HTMLLIElement>(null)
  useRegisterFluidHoverItem(register, index, ref)
  return <SidebarMenuItem ref={ref} className="fleet-sidebar-row">{children}</SidebarMenuItem>
}

interface AppSidebarProps {
  sessions: Session[]
  loading: boolean
  changing: boolean
  error: string
  running: string | null
  theme: ThemePreference
  onThemeChange: (theme: ThemePreference) => void
  onCreate: () => void
  onRename: (id: string, title: string) => Promise<Session>
  onDelete: (id: string) => Promise<void>
  onSettings: () => void
}

export function AppSidebar({
  sessions, loading, changing, error, running, theme, onThemeChange, onCreate, onRename, onDelete, onSettings,
}: AppSidebarProps) {
  const { state, isMobile, setOpenMobile } = useSidebar()
  const { pathname, key: locationKey } = useLocation()
  const [query, setQuery] = useState('')
  const [sessionsOpen, setSessionsOpen] = useState(true)
  const [visibleLimit, setVisibleLimit] = useState(sessionBatchSize)
  const [action, setAction] = useState<{ kind: 'rename' | 'delete'; session: Session } | null>(null)
  const [title, setTitle] = useState('')
  const [actionError, setActionError] = useState('')
  const actionTrigger = useRef<HTMLButtonElement>(null)
  const newButton = useRef<HTMLButtonElement>(null)
  const nextSessionFocus = useRef<string | null>(null)
  const search = query.trim().toLowerCase()
  const filtered = sessions.filter(({ title }) => title.toLowerCase().includes(search))
  const visibleIds = new Set(filtered
    .filter(({ id }) => pathname === `/sessions/${id}` || running === id)
    .map(({ id }) => id))
  for (const { id } of filtered) {
    if (visibleIds.size >= visibleLimit) break
    visibleIds.add(id)
  }
  const visibleSessions = filtered.filter(({ id }) => visibleIds.has(id))
  const nextSession = filtered.find(({ id }) => !visibleIds.has(id))
  const activeTheme = themeChoices.find(({ value }) => value === theme) ?? themeChoices[0]
  const collapsed = !isMobile && state === 'collapsed'

  useEffect(() => setOpenMobile(false), [isMobile, locationKey, setOpenMobile])

  useLayoutEffect(() => {
    if (!nextSessionFocus.current) return
    document.getElementById(`fleet-sidebar-session-${nextSessionFocus.current}`)?.focus()
    nextSessionFocus.current = null
  }, [visibleLimit])

  function openAction(kind: 'rename' | 'delete', session: Session) {
    setActionError('')
    setTitle(session.title)
    setAction({ kind, session })
  }

  async function commitAction() {
    if (!action || changing) return
    setActionError('')
    try {
      if (action.kind === 'delete') await onDelete(action.session.id)
      else await onRename(action.session.id, title.trim())
      setAction(null)
    } catch (failure) {
      setActionError(errorMessage(failure))
    }
  }

  const finalFocus = () => actionTrigger.current?.isConnected ? actionTrigger.current : newButton.current
  const closeAction = (open: boolean) => { if (!open && !changing) setAction(null) }

  return (
    <>
      <Sidebar
        variant="inset" collapsible="offcanvas"
        mobileFinalFocus={() => document.getElementById('fleet-sidebar-trigger')}
      >
        <div className="flex size-full min-h-0 flex-col" inert={collapsed} aria-hidden={collapsed || undefined}>
          <SidebarHeader className="shrink-0 px-2 pt-0 pb-2">
            <div className="flex h-(--pane-header-height) items-center gap-2 px-2 pb-px">
              <span className="flex-1 truncate text-sm font-semibold">Fleet RLM</span>
              <SidebarTrigger aria-label="Close navigation sidebar" title="Close navigation sidebar" className="md:hidden" />
            </div>
            <nav aria-label="Main navigation">
              <SidebarGroup className="p-0">
                <SidebarGroupContent>
                  <FluidMenu label="Workspace">
                    {(register) => (
                      <>
                        <FluidRow index={0} register={register}>
                          <SidebarMenuButton ref={newButton} type="button" disabled={changing} onClick={onCreate}>
                            <Plus aria-hidden="true" /><span>New</span>
                          </SidebarMenuButton>
                        </FluidRow>
                        <SidebarMenuItem className="my-1">
                          <InputGroup>
                            <InputGroupInput
                              type="search" aria-label="Filter sessions" placeholder="Search sessions…"
                              value={query} onChange={(event) => {
                                const next = event.currentTarget.value
                                setQuery(next)
                                setVisibleLimit(sessionBatchSize)
                                if (next.trim()) setSessionsOpen(true)
                              }}
                            />
                            <InputGroupAddon><Search aria-hidden="true" /></InputGroupAddon>
                          </InputGroup>
                        </SidebarMenuItem>
                        {navigation.map(({ to, label, icon: Icon }, index) => (
                          <FluidRow key={to} index={index + 1} register={register}>
                            <SidebarMenuButton render={<NavLink to={to} />} isActive={pathname === to || pathname.startsWith(to + '/')}>
                              <Icon aria-hidden="true" /><span>{label}</span>
                            </SidebarMenuButton>
                          </FluidRow>
                        ))}
                      </>
                    )}
                  </FluidMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            </nav>
          </SidebarHeader>

          <SidebarContent>
            <SidebarSeparator className="mx-3" />
            <Collapsible open={sessionsOpen} onOpenChange={setSessionsOpen} className="group/sessions">
              <SidebarGroup>
                <SidebarGroupLabel render={<CollapsibleTrigger type="button" />}>
                  Sessions
                  <ChevronDown aria-hidden="true" className="ml-auto group-data-[closed]/sessions:-rotate-90" />
                </SidebarGroupLabel>
                <CollapsibleContent>
                  <SidebarGroupContent>
                    <nav aria-label="Sessions">
                      <FluidMenu label="Session list">
                        {(register) => (
                          <>
                            {visibleSessions.map((session, index) => {
                              const href = `/sessions/${session.id}`
                              return (
                                <FluidRow key={session.id} index={index} register={register}>
                                  <SidebarMenuButton
                                    render={<NavLink to={href} id={`fleet-sidebar-session-${session.id}`} />}
                                    isActive={pathname === href} title={session.title}
                                  >
                                    <MessageSquare aria-hidden="true" />
                                    <span className="min-w-0 truncate">{session.title}</span>
                                    {running === session.id && (
                                      <span title="Run in progress" className="ml-auto shrink-0">
                                        <span className="block size-1.5 rounded-full bg-primary" aria-hidden="true" />
                                        <span className="sr-only">Run in progress</span>
                                      </span>
                                    )}
                                  </SidebarMenuButton>
                                  <DeferredDropdownMenu
                                    modal={false} align="end"
                                    trigger={(
                                      <SidebarMenuAction
                                        showOnHover disabled={changing}
                                        aria-label={`More options for ${session.title}`} title={`More options for ${session.title}`}
                                        onPointerDown={(event) => { actionTrigger.current = event.currentTarget }}
                                        onFocus={(event) => { actionTrigger.current = event.currentTarget }}
                                      ><Ellipsis aria-hidden="true" /></SidebarMenuAction>
                                    )}
                                    items={[
                                      { label: 'Rename', icon: <Pencil />, onSelect: () => openAction('rename', session) },
                                      { label: 'Delete', icon: <Trash2 />, variant: 'destructive', onSelect: () => openAction('delete', session) },
                                    ]}
                                  />
                                </FluidRow>
                              )
                            })}
                            {nextSession && (
                              <FluidRow index={visibleSessions.length} register={register}>
                                <SidebarMenuButton type="button" onClick={() => {
                                  nextSessionFocus.current = nextSession.id
                                  setVisibleLimit((limit) => limit + sessionBatchSize)
                                }}>
                                  <ChevronDown aria-hidden="true" /><span>Show more</span>
                                </SidebarMenuButton>
                              </FluidRow>
                            )}
                          </>
                        )}
                      </FluidMenu>
                      {loading && !sessions.length && (
                        <div role="status" aria-label="Loading sessions">
                          <SidebarMenuSkeleton showIcon className="motion-reduce:**:animate-none" />
                          <SidebarMenuSkeleton showIcon className="motion-reduce:**:animate-none" />
                          <SidebarMenuSkeleton showIcon className="motion-reduce:**:animate-none" />
                        </div>
                      )}
                      {!loading && !filtered.length && !error && (
                        <p className="px-2 py-3 text-xs text-muted-foreground">
                          {search ? 'No matching sessions.' : 'No sessions yet.'}
                        </p>
                      )}
                    </nav>
                  </SidebarGroupContent>
                </CollapsibleContent>
              </SidebarGroup>
            </Collapsible>
          </SidebarContent>

          <SidebarFooter>
            {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
            <SidebarSeparator className="mx-0" />
            <FluidMenu label="Preferences">
              {(register) => (
                <>
                  <FluidRow index={0} register={register}>
                    <SidebarMenuButton type="button" onClick={onSettings}>
                      <Settings aria-hidden="true" /><span>Settings</span>
                    </SidebarMenuButton>
                  </FluidRow>
                  <FluidRow index={1} register={register}>
                    <DeferredDropdownMenu
                      modal={false} side="top"
                      trigger={(
                        <SidebarMenuButton aria-label={`Theme: ${activeTheme.label}`}>
                          <activeTheme.icon aria-hidden="true" />
                          <span>Theme</span>
                          <span className="ml-auto text-xs text-muted-foreground">{activeTheme.label}</span>
                        </SidebarMenuButton>
                      )}
                      choices={{
                        value: theme,
                        options: themeChoices.map(({ value, label, icon: Icon }) => ({
                          value, label, icon: <Icon />, onSelect: () => onThemeChange(value),
                        })),
                      }}
                    />
                  </FluidRow>
                </>
              )}
            </FluidMenu>
          </SidebarFooter>
        </div>
      </Sidebar>
      <DeferredOverlay open={Boolean(action)}>
        <SessionActionDialogs
          action={action} finalFocus={finalFocus} closeAction={closeAction} title={title} setTitle={setTitle}
          changing={changing} actionError={actionError} commitAction={commitAction}
        />
      </DeferredOverlay>
    </>
  )
}
