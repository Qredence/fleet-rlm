import {
  Activity, Box, Cpu, Eye, EyeOff, KeyRound, Laptop, Moon, RefreshCw, RotateCcw, SlidersHorizontal, Sun,
} from 'lucide-react'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { cn } from 'cn'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/components/ui/input-group'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMediaQuery } from '@/hooks/use-media-query'
import type { ResolvedTheme, ThemePreference } from '@/hooks/use-theme'
import {
  errorMessage,
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

type SettingsTab = 'general' | 'models' | 'limits' | 'sandbox'

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
  const hintId = `${id}-hint`

  return (
    <Field data-disabled={inputProps.disabled || undefined}>
      <div className="flex items-center justify-between gap-2">
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        {overridden && <Badge variant="secondary">Overridden</Badge>}
      </div>
      <Input {...inputProps} id={id} aria-describedby={hintId} />
      <FieldDescription id={hintId}>{hint}</FieldDescription>
    </Field>
  )
}

function StatusTile({
  label,
  value,
  detail,
  className,
}: {
  label: string
  value: string
  detail?: string | null
  className?: string
}) {
  return (
    <Card size="sm" className={cn('min-w-0', className)}>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="truncate" title={value}>{value}</CardTitle>
        {detail && <CardDescription className="truncate" title={detail}>{detail}</CardDescription>}
      </CardHeader>
    </Card>
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

function SettingsDialogBody({
  api,
  token,
  onTokenChange,
  theme,
  systemTheme,
  onThemeChange,
  session,
  onUpdateSession,
}: Omit<SettingsDialogProps, 'open' | 'onOpenChange'>) {
  const verticalTabs = useMediaQuery('(min-width: 40rem)')
  const tokenId = useId()
  const tokenHintId = `${tokenId}-hint`
  const [tab, setTab] = useState<SettingsTab>('general')
  const [settingsView, setSettingsView] = useState<WorkspaceSettingsView | null>(null)
  const [draft, setDraft] = useState<SettingsDraft | null>(null)
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
      setNotice('Reset workspace settings to .env defaults.')
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setResetting(false)
    }
  }

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
        <div className="min-w-0 shrink-0 border-b p-2 sm:w-44 sm:border-r sm:border-b-0">
          <TabsList
            variant="line"
            className="w-full justify-start overflow-x-auto sm:items-stretch"
          >
            <TabsTrigger
              value="general"
              className="flex-none justify-start"
            >
              <Activity className="hidden sm:block" /> General
            </TabsTrigger>
            <TabsTrigger
              value="models"
              className="flex-none justify-start"
            >
              <Cpu className="hidden sm:block" /> Models
            </TabsTrigger>
            <TabsTrigger
              value="limits"
              className="flex-none justify-start"
            >
              <SlidersHorizontal className="hidden sm:block" /> RLM Limits
            </TabsTrigger>
            <TabsTrigger
              value="sandbox"
              aria-label="Sandbox & Tracing"
              className="flex-none justify-start"
            >
              <Box className="hidden sm:block" /> Sandbox<span className="hidden sm:inline">&amp; Tracing</span>
            </TabsTrigger>
          </TabsList>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <TabsContent value="general" className="grid gap-5">
            <section className="grid gap-2.5">
              <div>
                <h3 className="text-sm font-medium">Appearance</h3>
                <p className="text-xs text-muted-foreground">
                  Choose a light or dark monochrome palette, or follow your OS preference.
                </p>
              </div>
              <RadioGroup
                aria-label="Theme"
                value={theme}
                onValueChange={(value) => onThemeChange(value as ThemePreference)}
                className="grid-cols-1 sm:grid-cols-3"
              >
                {(
                  [
                    { id: 'light', label: 'Light', icon: Sun },
                    { id: 'dark', label: 'Dark', icon: Moon },
                    { id: 'system', label: `System (${systemTheme === 'dark' ? 'Dark' : 'Light'})`, icon: Laptop },
                  ] as const
                ).map(({ id, label, icon: Icon }) => (
                  <Field key={id} orientation="horizontal">
                    <RadioGroupItem value={id} id={`theme-${id}`} />
                    <FieldLabel htmlFor={`theme-${id}`}>
                      <Icon className="size-4 shrink-0" aria-hidden="true" />
                      {label}
                    </FieldLabel>
                  </Field>
                ))}
              </RadioGroup>
            </section>

            <Separator />

            <FieldGroup>
              <Field>
                <FieldLabel htmlFor={tokenId}>API Bearer Token</FieldLabel>
                <FieldDescription id={tokenHintId}>
                  Stored in this browser tab’s <code>sessionStorage</code> only.
                </FieldDescription>
                <div className="flex items-center gap-2">
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
              </Field>
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
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <StatusTile
                    label="Neon Postgres"
                    value={status.database_configured ? 'Configured' : 'Not configured'}
                  />
                  <StatusTile
                    label="Daytona Sandbox"
                    value={status.daytona_configured ? 'Configured' : 'Not configured'}
                    detail={status.daytona_volume_id ? `Volume: ${status.daytona_volume_id}` : undefined}
                  />
                  <StatusTile
                    label="LLM API Key"
                    value={status.llm_api_key_configured ? 'Configured' : 'Missing'}
                  />
                  <StatusTile
                    label="Bearer Auth"
                    value={status.bearer_auth_enabled ? 'Enabled' : 'Disabled'}
                  />
                  <StatusTile
                    label="MLflow Tracking URI"
                    value={status.mlflow_tracking_uri ? 'Connected' : 'Not configured'}
                    detail={status.mlflow_tracking_uri}
                  />
                  <StatusTile
                    label="Registered Skills"
                    value={`${status.skills_count} ${status.skills_count === 1 ? 'skill' : 'skills'}`}
                  />
                  <StatusTile
                    label="Max Concurrent Turns / Worker (.env-managed)"
                    value={`${status.max_concurrent_turns_per_worker} ${status.max_concurrent_turns_per_worker === 1 ? 'turn' : 'turns'}`}
                    detail="Read-only · FLEET_MAX_CONCURRENT_TURNS_PER_WORKER"
                    className="sm:col-span-2"
                  />
                  <Card size="sm" className="min-w-0 sm:col-span-2">
                    <CardHeader><CardTitle>Loaded Optimization Artifacts</CardTitle></CardHeader>
                    <CardContent>
                      <dl className="grid gap-1 font-mono text-[11px]">
                        <div className="flex justify-between gap-2">
                          <dt className="text-muted-foreground">optimized_program_path</dt>
                          <dd className="truncate text-foreground">{status.optimized_program_path ?? 'None'}</dd>
                        </div>
                        <div className="flex justify-between gap-2">
                          <dt className="text-muted-foreground">skill_selector_path</dt>
                          <dd className="truncate text-foreground">{status.skill_selector_path ?? 'None'}</dd>
                        </div>
                        <div className="flex justify-between gap-2">
                          <dt className="text-muted-foreground">memory_gate_path</dt>
                          <dd className="truncate text-foreground">{status.memory_gate_path ?? 'None'}</dd>
                        </div>
                      </dl>
                    </CardContent>
                  </Card>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {loading ? 'Loading infrastructure status…' : 'Status unavailable.'}
                </p>
              )}
            </section>
          </TabsContent>

          <TabsContent value="models" className="grid gap-4">
            {draft && defaults ? (
              <>
                <FieldGroup className="gap-3.5">
                  <div>
                    <h3 className="text-sm font-medium">Global Workspace Models</h3>
                    <p className="text-xs text-muted-foreground">
                      Persisted in Neon Postgres across workers. Endpoint base URL is read-only and managed via{' '}
                      <code className="font-mono">.env</code>.
                    </p>
                  </div>
                  <SettingField
                    label="Primary LLM Model"
                    hint={`Default: ${defaults.llm_model}`}
                    overridden={'llm_model' in overrides}
                    value={draft.llm_model}
                    onChange={(event) => updateField('llm_model', event.target.value)}
                  />
                  <SettingField
                    label="Sub-LLM Model"
                    hint={`Default: ${defaults.sub_llm_model}`}
                    overridden={'sub_llm_model' in overrides}
                    value={draft.sub_llm_model}
                    onChange={(event) => updateField('sub_llm_model', event.target.value)}
                  />
                  <SettingField
                    label="Decision Model (TypeSafe Jev)"
                    hint={`Default: ${defaults.decision_model}`}
                    overridden={'decision_model' in overrides}
                    value={draft.decision_model}
                    onChange={(event) => updateField('decision_model', event.target.value)}
                  />
                  {status && (
                    <StatusTile
                      label="LLM Base URL (.env-managed · read-only)"
                      value={status.llm_base_url ?? 'Provider default'}
                      detail="Configured via FLEET_LLM_BASE_URL in .env"
                    />
                  )}
                </FieldGroup>

                {session && (
                  <>
                    <Separator />
                    <FieldGroup className="gap-3">
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <h3 className="text-sm font-medium">Active Session Model Override</h3>
                          <p className="text-xs text-muted-foreground">
                            Overrides the primary LLM only for “{session.title}”.
                          </p>
                        </div>
                        {sessionModelValue.trim() && (
                          <Button
                            type="button"
                            size="xs"
                            variant="ghost"
                            onClick={() => setSessionModelDraft('')}
                          >
                            Clear override
                          </Button>
                        )}
                      </div>
                      <SettingField
                        label="Session model_override"
                        hint={`Inherits ${draft.llm_model} when empty`}
                        overridden={Boolean(session.model_override)}
                        value={sessionModelValue}
                        placeholder={draft.llm_model}
                        onChange={(event) => {
                          setNotice('')
                          setSessionModelDraft(event.target.value)
                        }}
                      />
                    </FieldGroup>
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
                <div>
                  <h3 className="text-sm font-medium">RLM Execution &amp; History Limits</h3>
                  <p className="text-xs text-muted-foreground">
                    Bounds for recursive iterations, tool calls, subagent depth, and bounded conversation history.
                  </p>
                </div>
                <FieldGroup className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
                  {NUMERIC_FIELDS.slice(0, 6).map(({ key, label, min, max }) => (
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
                  <h3 className="text-sm font-medium">Daytona Sandbox &amp; MLflow Tracing</h3>
                  <p className="text-xs text-muted-foreground">
                    Controls newly spawned Daytona sandboxes. Snapshot and MLflow experiment are read-only and
                    managed via <code className="font-mono">.env</code>.
                  </p>
                </div>
                <FieldGroup className="gap-3.5">
                  <SettingField
                    label="Daytona Target Region"
                    hint={`Default: ${defaults.daytona_target ?? 'None (provider default)'}`}
                    overridden={'daytona_target' in overrides}
                    value={draft.daytona_target}
                    placeholder="us (optional)"
                    onChange={(event) => updateField('daytona_target', event.target.value)}
                  />
                  <FieldGroup className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
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
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <StatusTile
                        label="Daytona Snapshot (.env-managed · read-only)"
                        value={status.daytona_snapshot}
                        detail="Configured via FLEET_DAYTONA_SNAPSHOT in .env"
                      />
                      <StatusTile
                        label="MLflow Experiment (.env-managed · read-only)"
                        value={status.mlflow_experiment}
                        detail="Configured via FLEET_MLFLOW_EXPERIMENT in .env"
                      />
                    </div>
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
      </Tabs>

      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t bg-muted/40 px-5 py-3.5">
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
    </form>
  )
}

export function SettingsDialog({
  open,
  onOpenChange,
  ...props
}: SettingsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85dvh] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <DialogHeader className="shrink-0 border-b px-5 py-4">
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Manage workspace appearance, model defaults, RLM execution limits, and sandbox configuration.
          </DialogDescription>
        </DialogHeader>
        {open && <SettingsDialogBody {...props} />}
      </DialogContent>
    </Dialog>
  )
}
