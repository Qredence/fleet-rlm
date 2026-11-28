import { Tabs as TabsPrimitive } from '@base-ui/react/tabs'
import {
  Activity, Box, Cpu, Eye, EyeOff, KeyRound, Monitor, Moon, PlugZap, RefreshCw, RotateCcw, SlidersHorizontal, Sun,
} from 'lucide-react'
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ModelSelector } from '@/components/agent/ModelSelector'
import { ChatGPTPlanSettings } from '@/components/agent/ChatGPTPlanSettings'
import { ProvidersSection } from '@/components/optimization/ProvidersSection'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DialogClose } from '@/components/ui/dialog'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/components/ui/input-group'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Separator } from '@/components/ui/separator'
import { Sidebar, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuButton, SidebarMenuItem } from '@/components/ui/sidebar'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { useMediaQuery } from '@/hooks/use-media-query'
import type { ResolvedTheme, ThemePreference } from '@/hooks/use-theme'
import {
  errorMessage,
  type ChatGPTPlanStatus,
  type FleetApi,
  type Session,
  type SessionUpdate,
  type WorkspaceSettingsFields,
  type WorkspaceSettingsView,
} from '@/lib/api'
import {
  buildSettingsPatch,
  hasDraftChanges,
  NUMERIC_FIELDS,
  toDraft,
  type SettingsDraft,
} from '@/lib/settings-draft'

type SettingsTab = 'general' | 'providers' | 'models' | 'limits' | 'sandbox'
const CUSTOM_ENVIRONMENT_MODEL = 'custom-environment-model:'

const settingsNavigation = [
  { value: 'general', label: 'General', description: 'Appearance, API access, and infrastructure status.', icon: Activity },
  { value: 'providers', label: 'Providers', description: 'Manage provider connections, credentials, and model discovery.', icon: PlugZap },
  { value: 'models', label: 'Models', description: 'Choose workspace defaults and the active session’s primary model.', icon: Cpu },
  { value: 'limits', label: 'RLM Limits', description: 'Set limits for recursive execution and conversation history.', icon: SlidersHorizontal },
  { value: 'sandbox', label: 'Sandbox & Tracing', description: 'Set the Python execution timeout and inspect Daytona and MLflow settings.', icon: Box },
] as const

function SettingRow({ label, description, id, children }: {
  label: string
  description?: ReactNode
  id?: string
  children: ReactNode
}) {
  return (
    <Field
      orientation="horizontal"
      className="justify-between gap-4 border-b border-border/60 py-4 last:border-b-0 @max-md:flex-col @max-md:items-start"
    >
      <div className="min-w-0">
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        {description && <FieldDescription id={id ? `${id}-description` : undefined}>{description}</FieldDescription>}
      </div>
      <div className="w-fit min-w-0 max-w-full">{children}</div>
    </Field>
  )
}

function SettingField({
  label,
  hint,
  overridden,
  ...inputProps
}: React.ComponentProps<typeof Input> & {
  label: string
  hint: string
  overridden?: boolean
}) {
  const id = useId()

  return (
    <SettingRow label={label} id={id} description={
      <>{hint}{overridden && <> <Badge variant="secondary">Overridden</Badge></>}</>
    }>
      <Input {...inputProps} id={id} aria-describedby={`${id}-description`} className="w-24" />
    </SettingRow>
  )
}

function StatusRow({ label, value, detail }: {
  label: string
  value: string
  detail?: string | null
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border/60 py-4 last:border-b-0">
      <dt className="min-w-0">
        <span className="font-medium">{label}</span>
        {detail && <span className="block [overflow-wrap:anywhere] text-xs text-muted-foreground">{detail}</span>}
      </dt>
      <dd className="max-w-1/2 shrink-0 text-right [overflow-wrap:anywhere] text-muted-foreground">{value}</dd>
    </div>
  )
}

export interface SettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  api: FleetApi
  token: string
  onTokenChange: (token: string) => void
  theme: ThemePreference
  systemTheme: ResolvedTheme
  onThemeChange: (theme: ThemePreference) => void
  session?: Session
  onUpdateSession: (id: string, changes: SessionUpdate) => Promise<boolean>
}

export function SettingsDialogBody({
  api,
  token,
  onTokenChange,
  theme,
  onThemeChange,
  session,
  onUpdateSession,
}: Omit<SettingsDialogProps, 'open' | 'onOpenChange'>) {
  const verticalTabs = useMediaQuery('(min-width: 40rem)')
  const tokenId = useId()
  const tokenHintId = `${tokenId}-description`
  const [tab, setTab] = useState<SettingsTab>('general')
  const [result, setResult] = useState<Awaited<ReturnType<FleetApi['listOptimizationProviders']>> | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [version, refresh] = useState(0)
  const [chatGPTPlan, setChatGPTPlan] = useState<ChatGPTPlanStatus | null>(null)
  const [chatGPTError, setChatGPTError] = useState('')
  const planRequest = useRef<AbortController | null>(null)
  const loadChatGPTPlan = useCallback(async () => {
    planRequest.current?.abort()
    const request = new AbortController()
    planRequest.current = request
    try {
      const result = await api.chatGPTPlan(request.signal)
      if (request.signal.aborted) return
      setChatGPTPlan(result.enabled ? result : null)
      setChatGPTError('')
    } catch (failure) {
      if (!request.signal.aborted) setChatGPTError(errorMessage(failure))
    }
  }, [api])
  useEffect(() => {
    if (tab !== 'models' && tab !== 'providers') return
    function refreshPlan() {
      void loadChatGPTPlan()
    }
    refreshPlan()
    window.addEventListener('focus', refreshPlan)
    return () => {
      planRequest.current?.abort()
      window.removeEventListener('focus', refreshPlan)
    }
  }, [tab, loadChatGPTPlan])
  useEffect(() => {
    if (tab !== 'models' && tab !== 'providers') return
    const request = new AbortController()
    void api.listOptimizationProviders(request.signal).then(data => {
      if (!request.signal.aborted) { setResult(data); setFailure(null) }
    }).catch(reason => { if (!request.signal.aborted) setFailure(errorMessage(reason)) })
    return () => request.abort()
  }, [api, version, tab])
  const [settingsView, setSettingsView] = useState<WorkspaceSettingsView | null>(null)
  const [draft, setDraft] = useState<SettingsDraft | null>(null)
  const [customModelRole, setCustomModelRole] = useState<'llm_model' | 'sub_llm_model' | null>(null)
  const [sessionModelDraft, setSessionModelDraft] = useState<string | null>(null)
  const [tokenDraft, setTokenDraft] = useState(token)
  const [showToken, setShowToken] = useState(false)
  const [readyStatus, setReadyStatus] = useState<'checking' | 'ready' | 'error'>('checking')
  const [readyError, setReadyError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const settingsRequestRef = useRef<AbortController | null>(null)
  const effectiveRef = useRef<WorkspaceSettingsFields | null>(null)
  const sessionModelValue = sessionModelDraft ?? (session?.model_override ?? '')

  const fetchStatusAndSettings = useCallback((signal: AbortSignal) => {
    void api.getSettings(signal)
      .then((view) => {
        if (signal.aborted) return
        const previousEffective = effectiveRef.current
        effectiveRef.current = view.effective
        setError('')
        setSettingsView(view)
        setDraft((current) => {
          if (!current || !previousEffective) return toDraft(view.effective)
          return hasDraftChanges(previousEffective, current) ? current : toDraft(view.effective)
        })
      })
      .catch((failure) => {
        if (!signal.aborted) setError(errorMessage(failure))
      })
      .finally(() => {
        if (!signal.aborted) setLoading(false)
      })

    void api.readiness(signal)
      .then(() => {
        if (!signal.aborted) {
          setReadyStatus('ready')
          setReadyError('')
        }
      })
      .catch((failure) => {
        if (!signal.aborted) {
          setReadyStatus('error')
          setReadyError(errorMessage(failure))
        }
      })
  }, [api])

  const loadSettings = useCallback(() => {
    settingsRequestRef.current?.abort()
    const request = new AbortController()
    settingsRequestRef.current = request
    setLoading(true)
    setError('')
    setReadyStatus('checking')
    setReadyError('')
    fetchStatusAndSettings(request.signal)
  }, [fetchStatusAndSettings])

  useEffect(() => {
    settingsRequestRef.current?.abort()
    const request = new AbortController()
    settingsRequestRef.current = request
    fetchStatusAndSettings(request.signal)
    return () => {
      request.abort()
      settingsRequestRef.current?.abort()
      settingsRequestRef.current = null
    }
  }, [fetchStatusAndSettings])

  function updateField<K extends keyof SettingsDraft>(key: K, value: string) {
    setNotice('')
    setError('')
    setDraft((current) => (current ? { ...current, [key]: value } : current))
  }

  const sessionOverrideChanged = Boolean(
    session && (sessionModelValue.trim() || null) !== (session.model_override ?? null),
  )

  const hasSettingsChanges = Boolean(
    settingsView && draft && hasDraftChanges(settingsView.effective, draft),
  )

  const hasChanges = hasSettingsChanges || sessionOverrideChanged
  const hasOverrides = Boolean(settingsView && Object.keys(settingsView.overrides).length > 0)

  async function handleSave(event: React.FormEvent) {
    event.preventDefault()
    if (!hasChanges) return
    setError('')
    setNotice('')

    const { patch, validationError } =
      settingsView && draft ? buildSettingsPatch(settingsView.effective, draft) : { patch: {} }
    if (validationError) {
      setError(validationError)
      return
    }

    setSaving(true)
    try {
      if (Object.keys(patch).length > 0) {
        const updated = await api.updateSettings(patch)
        effectiveRef.current = updated.effective
        setSettingsView(updated)
        setDraft(toDraft(updated.effective))
        setCustomModelRole(null)
      }
      if (session && sessionOverrideChanged) {
        const nextOverride = sessionModelValue.trim() || null
        const ok = await onUpdateSession(session.id, { model_override: nextOverride })
        if (!ok) {
          setError('Failed to update session model override.')
          return
        }
        setSessionModelDraft(null)
      }
      setNotice('Settings saved.')
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSaving(false)
    }
  }

  async function handleResetDefaults() {
    setResetting(true)
    setError('')
    setNotice('')
    try {
      const reset = await api.resetSettings()
      effectiveRef.current = reset.effective
      setSettingsView(reset)
      setDraft(toDraft(reset.effective))
      setCustomModelRole(null)
      setNotice('Reset workspace settings to .env defaults.')
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setResetting(false)
    }
  }

  const currentSection = settingsNavigation.find(section => section.value === tab) ?? settingsNavigation[0]
  const themeIcons = { system: Monitor, light: Sun, dark: Moon }
  const status = settingsView?.status
  const defaults = settingsView?.defaults
  const overrides = settingsView?.overrides ?? {}

  return (
    <form onSubmit={(event) => void handleSave(event)} className="flex min-h-0 flex-1 flex-col">
      <Tabs
        value={tab}
        onValueChange={(value) => setTab(value as SettingsTab)}
        orientation={verticalTabs ? 'vertical' : 'horizontal'}
        className="min-h-0 flex-1 gap-0"
      >
        <Sidebar collapsible="none" className="hidden shrink-0 border-r border-border/60 bg-[rgb(var(--overlay)/0.03)] text-foreground sm:flex sm:w-52">
          <SidebarHeader className="px-4 pt-5 pb-2">Settings</SidebarHeader>
          <SidebarGroup>
            <SidebarGroupLabel>Workspace</SidebarGroupLabel>
            <SidebarGroupContent>
              <TabsPrimitive.List
                aria-label="Settings sections"
                render={<SidebarMenu className="gap-1" />}
              >
                {settingsNavigation.map(({ value, label, icon: Icon }) => (
                  <SidebarMenuItem key={value} role="presentation" className="shrink-0">
                    <TabsPrimitive.Tab
                      value={value}
                      render={<SidebarMenuButton type="button" isActive={tab === value} />}
                    >
                      <Icon aria-hidden="true" />
                      <span>{label}</span>
                    </TabsPrimitive.Tab>
                  </SidebarMenuItem>
                ))}
              </TabsPrimitive.List>
            </SidebarGroupContent>
          </SidebarGroup>
        </Sidebar>

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <header className="shrink-0 px-5 pt-5 pb-3 sm:px-6">
            <div className="mb-3 grid gap-2 pr-8 sm:hidden">
              <p>Settings</p>
              <Select value={tab} onValueChange={value => setTab(value as SettingsTab)}>
                <SelectTrigger aria-label="Settings section" icon={currentSection.icon} />
                <SelectContent><SelectGroup>
                  {settingsNavigation.map(({ value, label, icon }, index) => (
                    <SelectItem key={value} value={value} index={index} icon={icon}>{label}</SelectItem>
                  ))}
                </SelectGroup></SelectContent>
              </Select>
            </div>
            <h2 className="pr-8 font-medium">{currentSection.label}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{currentSection.description}</p>
          </header>

          <div key={tab} className="@container min-h-0 min-w-0 flex-1 overflow-y-auto px-5 pt-1 pb-5 sm:px-6">
          <TabsContent value="general" className="grid gap-5">
            <FieldGroup className="gap-0">
              <SettingRow label="Theme" id="settings-theme" description="Follow your system preference or choose a theme.">
                <Select value={theme} onValueChange={value => onThemeChange(value as ThemePreference)}>
                  <SelectTrigger id="settings-theme" aria-describedby="settings-theme-description" icon={themeIcons[theme]} placeholder="Theme" />
                  <SelectContent><SelectGroup>
                    <SelectItem index={0} value="system" icon={Monitor}>System</SelectItem>
                    <SelectItem index={1} value="light" icon={Sun}>Light</SelectItem>
                    <SelectItem index={2} value="dark" icon={Moon}>Dark</SelectItem>
                  </SelectGroup></SelectContent>
                </Select>
              </SettingRow>

              <SettingRow label="API Bearer Token" id={tokenId} description={
                <>Stored in this browser tab’s <code>sessionStorage</code> only.</>
              }>
                <div className="flex max-w-full items-center gap-2">
                  <InputGroup>
                    <InputGroupInput
                      id={tokenId}
                      type={showToken ? 'text' : 'password'}
                      autoComplete="off"
                      placeholder="Optional bearer token"
                      aria-describedby={tokenHintId}
                      value={tokenDraft}
                      onChange={(event) => setTokenDraft(event.target.value)}
                    />
                    <InputGroupAddon><KeyRound aria-hidden="true" /></InputGroupAddon>
                    <InputGroupAddon align="inline-end">
                      <InputGroupButton
                        size="icon-xs"
                        aria-label={showToken ? 'Hide token' : 'Show token'}
                        onClick={() => setShowToken((current) => !current)}
                      >
                        {showToken ? <EyeOff /> : <Eye />}
                      </InputGroupButton>
                    </InputGroupAddon>
                  </InputGroup>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={tokenDraft.trim() === token}
                    onClick={() => {
                      const cleaned = tokenDraft.trim()
                      setTokenDraft(cleaned)
                      onTokenChange(cleaned)
                      setNotice('API bearer token updated for this tab.')
                    }}
                  >
                    Apply
                  </Button>
                </div>
              </SettingRow>
            </FieldGroup>

            <Separator />

            <section className="grid gap-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-medium">Infrastructure Status</h3>
                  <p className="text-xs text-muted-foreground">
                    Live readiness and read-only backend wiring from <code className="font-mono">/api/settings</code>.
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge
                    variant={readyStatus === 'error' ? 'destructive' : readyStatus === 'ready' ? 'default' : 'secondary'}
                  >
                    {readyStatus === 'ready' ? 'Ready (/readyz)' : readyStatus === 'checking' ? 'Checking…' : 'Unavailable'}
                  </Badge>
                  <Button
                    type="button"
                    size="icon-xs"
                    variant="ghost"
                    aria-label="Refresh status"
                    disabled={loading}
                    onClick={() => loadSettings()}
                  >
                    <RefreshCw className={loading ? 'animate-spin' : ''} />
                  </Button>
                </div>
              </div>

              {readyError && (
                <Alert variant="destructive">
                  <AlertDescription>Readiness check failed: {readyError}</AlertDescription>
                </Alert>
              )}

              {status ? (
                <>
                  <dl>
                    <StatusRow
                      label="Neon Postgres"
                      value={status.database_configured ? 'Configured' : 'Not configured'}
                    />
                    <StatusRow
                      label="Daytona Sandbox"
                      value={status.daytona_configured ? 'Configured' : 'Not configured'}
                      detail={status.daytona_region ?? 'Provider default region'}
                    />
                    <StatusRow
                      label="LLM API Key"
                      value={status.llm_api_key_configured ? 'Configured' : 'Missing'}
                    />
                    <StatusRow
                      label="Bearer Auth"
                      value={status.bearer_auth_enabled ? 'Enabled' : 'Disabled'}
                    />
                    <StatusRow
                      label="MLflow Tracking URI"
                      value={status.mlflow_tracking_uri ? 'Connected' : 'Not configured'}
                      detail={status.mlflow_tracking_uri}
                    />
                    <StatusRow
                      label="Registered Skills"
                      value={`${status.skills_count} ${status.skills_count === 1 ? 'skill' : 'skills'}`}
                    />
                    <StatusRow
                      label="Max Concurrent Turns / Worker (.env-managed)"
                      value={`${status.max_concurrent_turns_per_worker} ${status.max_concurrent_turns_per_worker === 1 ? 'turn' : 'turns'}`}
                      detail="Read-only · FLEET_MAX_CONCURRENT_TURNS_PER_WORKER"
                    />
                  </dl>
                  <div>
                    <h4 className="text-sm font-medium">Loaded Optimization Artifacts</h4>
                    <dl>
                      <StatusRow label="Optimized program" value={status.optimized_program_path ?? 'None'} />
                      <StatusRow label="Skill selector" value={status.skill_selector_path ?? 'None'} />
                      <StatusRow label="Memory gate" value={status.memory_gate_path ?? 'None'} />
                    </dl>
                  </div>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {loading ? 'Loading infrastructure status…' : 'Status unavailable.'}
                </p>
              )}
            </section>
          </TabsContent>

          <TabsContent value="providers">
            {chatGPTError && <Alert variant="destructive"><AlertDescription>{chatGPTError}</AlertDescription></Alert>}
            {chatGPTPlan && <ChatGPTPlanSettings api={api} status={chatGPTPlan} onChange={loadChatGPTPlan} />}
            <ProvidersSection
              layout="rows"
              api={api} providers={result?.providers ?? null}
              encryptionConfigured={result?.encryption_configured ?? false}
              allowPrivateEndpoints={result?.allow_private_endpoints ?? false}
              loading={!result && !failure} loadError={failure}
              onRetry={() => refresh(value => value + 1)}
              onChanged={() => refresh(value => value + 1)}
              onOpenModels={() => setTab('models')}
            />
          </TabsContent>

          <TabsContent value="models" className="grid grid-cols-1 gap-4">
            {failure && <Alert variant="destructive"><AlertDescription>{failure}</AlertDescription></Alert>}
            {chatGPTError && <Alert variant="destructive"><AlertDescription>{chatGPTError}</AlertDescription></Alert>}
            {chatGPTPlan && <ChatGPTPlanSettings api={api} status={chatGPTPlan} onChange={loadChatGPTPlan} modelsOnly />}
            {draft && defaults ? (
              <>
                <FieldGroup>
                  <div>
                    <h3 className="text-sm font-medium">Global Workspace Models</h3>
                    <p className="text-xs text-muted-foreground">
                      Choose a provider and model for each role. Save changes to apply the defaults across the workspace.
                    </p>
                  </div>
                  <FieldGroup className="gap-0">
                  {(['llm_model', 'sub_llm_model', 'decision_model'] as const).map(role => {
                    const decision = role === 'decision_model'
                    const current = draft[role]
                    const environmentDefault = defaults[role]
                    const options = new Map<string, string>()
                    if (!decision || /^typesafe:jev(?:-[^\s/]+)?$/.test(environmentDefault)) {
                      options.set(environmentDefault, `Environment default · ${environmentDefault}`)
                    }
                    if (decision && !options.has('typesafe:jev-latest')) {
                      options.set('typesafe:jev-latest', 'TypeSafe · jev-latest')
                    }
                    for (const provider of result?.providers ?? []) {
                      if (decision && provider.dialect !== 'typesafe') continue
                      const names = new Set(provider.cached_models.map(model => model.id))
                      if (provider.default_model) names.add(provider.default_model)
                      for (const name of names) {
                        if (decision && !/^(?:typesafe[:/])?jev(?:-[^\s/]+)?$/.test(name)) continue
                        options.set(`provider/${provider.id}/${name}`, `${provider.name} · ${name}`)
                      }
                    }
                    const savedModel = settingsView?.effective[role]
                    if (savedModel && !options.has(savedModel)) {
                      options.set(savedModel, `Current model · ${savedModel}`)
                    }
                    if (current.trim() && !options.has(current)) options.set(current, current)
                    if (!decision) options.set(CUSTOM_ENVIRONMENT_MODEL, 'Enter environment model name…')
                    const label = {
                      llm_model: 'Primary LLM Model',
                      sub_llm_model: 'Sub-LLM Model',
                      decision_model: 'Decision Model (TypeSafe Jev)',
                    }[role]
                    const enteringModel = customModelRole === role
                    return <SettingRow key={role} label={label} id={`workspace-${role}`}>
                      <div className="grid w-56 max-w-full grid-cols-1 gap-2">
                        <Select value={enteringModel ? CUSTOM_ENVIRONMENT_MODEL : current} onValueChange={value => {
                          if (role !== 'decision_model' && value === CUSTOM_ENVIRONMENT_MODEL) {
                            setCustomModelRole(role)
                            if (current.startsWith('provider/')) updateField(role, environmentDefault)
                            return
                          }
                          if (enteringModel) setCustomModelRole(null)
                          updateField(role, value)
                        }}>
                          <SelectTrigger id={`workspace-${role}`} aria-label={label}
                            placeholder="Select a model" className="w-full" />
                          <SelectContent>
                            <SelectGroup>{[...options].map(([value, title], index) => (
                              <SelectItem key={value} value={value} index={index}>{title}</SelectItem>
                            ))}</SelectGroup>
                          </SelectContent>
                        </Select>
                        {enteringModel && <Input
                          aria-label={`${label} environment model name`}
                          placeholder="e.g. openai/gpt-5-mini"
                          value={current}
                          onChange={event => updateField(role, event.target.value)}
                        />}
                      </div>
                    </SettingRow>
                  })}
                  </FieldGroup>
                  {status && (
                    <dl>
                      <StatusRow
                        label="LLM Base URL (.env-managed · read-only)"
                        value={status.llm_base_url ?? 'Provider default'}
                        detail="Configured via FLEET_LLM_BASE_URL in .env"
                      />
                    </dl>
                  )}
                </FieldGroup>

                {session && (
                  <>
                    <Separator />
                    <SettingRow label="Active Session Model Override"
                      description={`Overrides the primary LLM only for “${session.title}”.`}>
                      <div className="flex flex-wrap items-center gap-2">
                        <ModelSelector api={api} model={sessionModelValue || null}
                          disabled={loading} saving={saving} onChange={model => {
                            setNotice('')
                            setSessionModelDraft(model ?? '')
                          }} />
                        {sessionModelValue.trim() && (
                          <Button type="button" size="xs" variant="ghost" onClick={() => setSessionModelDraft('')}>
                            Clear override
                          </Button>
                        )}
                      </div>
                    </SettingRow>
                  </>
                )}
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                {loading ? 'Loading model settings…' : 'Unable to load settings.'}
              </p>
            )}
          </TabsContent>

          <TabsContent value="limits" className="grid gap-4">
            {draft && defaults ? (
              <>
                <FieldGroup className="gap-0">
                  {NUMERIC_FIELDS.slice(0, 6).map(({ key, label, min, max }) => {
                    const exactEntry = key === 'rlm_max_output_chars' || key === 'history_max_chars'
                    const parsed = Number(draft[key])
                    const valid = Boolean(draft[key].trim()) && Number.isInteger(parsed) && parsed >= min && parsed <= max
                    return (
                      <SettingRow key={key} label={label} description={
                        <>Default: {defaults[key]} · Range: {min}–{max}
                          {key in overrides && <> <Badge variant="secondary">Overridden</Badge></>}
                        </>
                      }>
                        <div className="flex max-w-full items-center gap-3">
                          <Slider
                            size="default"
                            variant={key === 'rlm_max_depth' ? 'pips' : 'scrubber'}
                            aria-label={label}
                            className="w-48 min-w-0"
                            value={valid ? parsed : defaults[key]}
                            min={min}
                            max={max}
                            step={1}
                            onChange={value => {
                              if (typeof value === 'number') updateField(key, String(value))
                            }}
                          />
                          {exactEntry && (
                            <Input
                              type="number"
                              aria-label={`${label} exact value`}
                              aria-invalid={!valid}
                              className="w-24"
                              min={min}
                              max={max}
                              step={1}
                              required
                              value={draft[key]}
                              onChange={event => updateField(key, event.target.value)}
                            />
                          )}
                        </div>
                      </SettingRow>
                    )
                  })}
                </FieldGroup>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                {loading ? 'Loading RLM limits…' : 'Unable to load settings.'}
              </p>
            )}
          </TabsContent>

          <TabsContent value="sandbox" className="grid gap-4">
            {draft && defaults ? (
              <>
                <div>
                  <p className="text-xs text-muted-foreground">
                    Sandbox, storage, and tracing infrastructure are managed
                    via <code className="font-mono">.env</code>.
                  </p>
                </div>
                <FieldGroup className="gap-3.5">
                  <FieldGroup className="gap-0">
                    {NUMERIC_FIELDS.slice(6).map(({ key, label, min, max }) => (
                      <SettingField
                        key={key}
                        label={label}
                        hint={`Default: ${defaults[key]} · Range: ${min}–${max}`}
                        overridden={key in overrides}
                        type="number"
                        min={min}
                        max={max}
                        step={1}
                        value={draft[key]}
                        onChange={(event) => updateField(key, event.target.value)}
                      />
                    ))}
                  </FieldGroup>
                  {status && (
                    <dl>
                      <StatusRow
                        label="Daytona Image"
                        value={status.daytona_image}
                        detail={`${status.daytona_region ?? 'Default region'} · ${status.daytona_idle_timeout_minutes} min idle · ${status.daytona_max_lifetime_minutes} min lifetime`}
                      />
                      <StatusRow
                        label="Neon Object Storage"
                        value={status.storage_bucket}
                      />
                      <StatusRow
                        label="MLflow Experiment (.env-managed · read-only)"
                        value={status.mlflow_experiment}
                        detail="Configured via FLEET_MLFLOW_EXPERIMENT in .env"
                      />
                    </dl>
                  )}
                </FieldGroup>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                {loading ? 'Loading sandbox settings…' : 'Unable to load settings.'}
              </p>
            )}
          </TabsContent>
          </div>
          <div data-slot="settings-footer" className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t px-5 py-3 sm:px-6">
            <div className="flex min-w-0 items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={resetting || saving || !hasOverrides}
                onClick={() => void handleResetDefaults()}
              >
                <RotateCcw data-icon="inline-start" />
                {resetting ? 'Resetting…' : 'Reset defaults'}
              </Button>
              {error && (
                <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
              )}
              {!error && notice && (
                <p role="status" className="truncate text-xs text-muted-foreground">
                  {notice}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <DialogClose render={<Button type="button" variant="ghost" size="sm" />}>
                Close
              </DialogClose>
              <Button
                type="submit"
                size="sm"
                disabled={saving || resetting || !hasChanges}
              >
                {saving ? 'Saving…' : 'Save changes'}
              </Button>
            </div>
          </div>
        </div>
      </Tabs>
    </form>
  )
}
