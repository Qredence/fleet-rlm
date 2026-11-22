import {
  Activity, Box, Cpu, Eye, EyeOff, KeyRound, Laptop, Moon, RefreshCw, RotateCcw, SlidersHorizontal, Sun,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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

function FieldRow({
  label,
  hint,
  overridden,
  children,
}: {
  label: string
  hint?: string
  overridden?: boolean
  children: React.ReactNode
}) {
  return (
    <label className="grid gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">{label}</span>
        {overridden && (
          <Badge variant="secondary" className="rounded-full text-[10px] font-normal">
            Overridden
          </Badge>
        )}
      </div>
      {children}
      {hint && <span className="font-mono text-[11px] text-muted-foreground">{hint}</span>}
    </label>
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
    <div className={`grid min-w-0 gap-0.5 rounded-xl border bg-card p-3${className ? ` ${className}` : ''}`}>
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="truncate text-xs font-medium text-foreground" title={value}>
        {value}
      </span>
      {detail && (
        <span className="truncate font-mono text-[11px] text-muted-foreground" title={detail}>
          {detail}
        </span>
      )}
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
            orientation="vertical"
            className="flex min-h-0 flex-1 flex-col gap-0 sm:flex-row"
          >
            <div className="shrink-0 border-b bg-muted/30 p-2 sm:w-48 sm:border-r sm:border-b-0">
              <TabsList
                variant="line"
                className="flex w-full flex-row justify-start gap-1 overflow-x-auto bg-transparent p-0 sm:flex-col"
              >
                <TabsTrigger
                  value="general"
                  className="h-9 flex-none justify-start gap-2 rounded-xl px-3 text-xs data-active:bg-background data-active:shadow-xs after:hidden sm:w-full"
                >
                  <Activity className="size-3.5" /> General
                </TabsTrigger>
                <TabsTrigger
                  value="models"
                  className="h-9 flex-none justify-start gap-2 rounded-xl px-3 text-xs data-active:bg-background data-active:shadow-xs after:hidden sm:w-full"
                >
                  <Cpu className="size-3.5" /> Models
                </TabsTrigger>
                <TabsTrigger
                  value="limits"
                  className="h-9 flex-none justify-start gap-2 rounded-xl px-3 text-xs data-active:bg-background data-active:shadow-xs after:hidden sm:w-full"
                >
                  <SlidersHorizontal className="size-3.5" /> RLM Limits
                </TabsTrigger>
                <TabsTrigger
                  value="sandbox"
                  className="h-9 flex-none justify-start gap-2 rounded-xl px-3 text-xs data-active:bg-background data-active:shadow-xs after:hidden sm:w-full"
                >
                  <Box className="size-3.5" /> Sandbox &amp; Tracing
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
                  <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Theme">
                    {(
                      [
                        { id: 'light', label: 'Light', icon: Sun },
                        { id: 'dark', label: 'Dark', icon: Moon },
                        {
                          id: 'system',
                          label: `System (${systemTheme === 'dark' ? 'Dark' : 'Light'})`,
                          icon: Laptop,
                        },
                      ] as const
                    ).map(({ id, label, icon: Icon }) => {
                      const selected = theme === id
                      return (
                        <Button
                          key={id}
                          type="button"
                          variant={selected ? 'default' : 'outline'}
                          size="sm"
                          role="radio"
                          aria-checked={selected}
                          className="h-9 justify-center gap-1.5 rounded-xl text-xs"
                          onClick={() => onThemeChange(id)}
                        >
                          <Icon className="size-3.5" />
                          <span className="truncate">{label}</span>
                        </Button>
                      )
                    })}
                  </div>
                </section>

                <Separator />

                <section className="grid gap-2.5">
                  <div>
                    <h3 className="text-sm font-medium">API Bearer Token</h3>
                    <p className="text-xs text-muted-foreground">
                      Stored in this browser tab’s <code className="font-mono">sessionStorage</code> only.
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="relative flex-1">
                      <KeyRound
                        className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <Input
                        type={showToken ? 'text' : 'password'}
                        autoComplete="off"
                        placeholder="Optional bearer token"
                        aria-label="API Bearer Token"
                        value={tokenDraft}
                        onChange={(event) => setTokenDraft(event.target.value)}
                        className="h-9 rounded-xl pr-8 pl-8 font-mono text-xs"
                      />
                      <button
                        type="button"
                        aria-label={showToken ? 'Hide token' : 'Show token'}
                        onClick={() => setShowToken((current) => !current)}
                        className="absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                      >
                        {showToken ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                      </button>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-9 rounded-xl"
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
                </section>

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
                        className="rounded-full"
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
                    <p role="alert" className="text-xs text-destructive">
                      Readiness check failed: {readyError}
                    </p>
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
                      <div className="grid gap-1.5 rounded-xl border bg-card p-3 sm:col-span-2">
                        <span className="text-[11px] text-muted-foreground">Loaded Optimization Artifacts</span>
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
                      </div>
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
                    <div className="grid gap-3.5">
                      <div>
                        <h3 className="text-sm font-medium">Global Workspace Models</h3>
                        <p className="text-xs text-muted-foreground">
                          Persisted in Neon Postgres across workers. Endpoint base URL is read-only and managed via{' '}
                          <code className="font-mono">.env</code>.
                        </p>
                      </div>
                      <FieldRow
                        label="Primary LLM Model"
                        hint={`Default: ${defaults.llm_model}`}
                        overridden={'llm_model' in overrides}
                      >
                        <Input
                          value={draft.llm_model}
                          onChange={(event) => updateField('llm_model', event.target.value)}
                          className="h-9 rounded-xl font-mono text-xs"
                        />
                      </FieldRow>
                      <FieldRow
                        label="Sub-LLM Model"
                        hint={`Default: ${defaults.sub_llm_model}`}
                        overridden={'sub_llm_model' in overrides}
                      >
                        <Input
                          value={draft.sub_llm_model}
                          onChange={(event) => updateField('sub_llm_model', event.target.value)}
                          className="h-9 rounded-xl font-mono text-xs"
                        />
                      </FieldRow>
                      <FieldRow
                        label="Decision Model (TypeSafe Jev)"
                        hint={`Default: ${defaults.decision_model}`}
                        overridden={'decision_model' in overrides}
                      >
                        <Input
                          value={draft.decision_model}
                          onChange={(event) => updateField('decision_model', event.target.value)}
                          className="h-9 rounded-xl font-mono text-xs"
                        />
                      </FieldRow>
                      {status && (
                        <StatusTile
                          label="LLM Base URL (.env-managed · read-only)"
                          value={status.llm_base_url ?? 'Provider default'}
                          detail="Configured via FLEET_LLM_BASE_URL in .env"
                        />
                      )}
                    </div>

                    {session && (
                      <>
                        <Separator />
                        <div className="grid gap-3">
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
                          <FieldRow
                            label="Session model_override"
                            hint={`Inherits ${draft.llm_model} when empty`}
                            overridden={Boolean(session.model_override)}
                          >
                            <Input
                              value={sessionModelValue}
                              placeholder={draft.llm_model}
                              onChange={(event) => {
                                setNotice('')
                                setSessionModelDraft(event.target.value)
                              }}
                              className="h-9 rounded-xl font-mono text-xs"
                            />
                          </FieldRow>
                        </div>
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
                    <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
                      {NUMERIC_FIELDS.slice(0, 6).map(({ key, label, min, max }) => (
                        <FieldRow
                          key={key}
                          label={label}
                          hint={`Default: ${defaults[key]} · Range: ${min}–${max}`}
                          overridden={key in overrides}
                        >
                          <Input
                            type="number"
                            min={min}
                            max={max}
                            step={1}
                            value={draft[key]}
                            onChange={(event) => updateField(key, event.target.value)}
                            className="h-9 rounded-xl font-mono text-xs tabular-nums"
                          />
                        </FieldRow>
                      ))}
                    </div>
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
                    <div className="grid gap-3.5">
                      <FieldRow
                        label="Daytona Target Region"
                        hint={`Default: ${defaults.daytona_target ?? 'None (provider default)'}`}
                        overridden={'daytona_target' in overrides}
                      >
                        <Input
                          value={draft.daytona_target}
                          placeholder="us (optional)"
                          onChange={(event) => updateField('daytona_target', event.target.value)}
                          className="h-9 rounded-xl font-mono text-xs"
                        />
                      </FieldRow>
                      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
                        {NUMERIC_FIELDS.slice(6).map(({ key, label, min, max }) => (
                          <FieldRow
                            key={key}
                            label={label}
                            hint={`Default: ${defaults[key]} · Range: ${min}–${max}`}
                            overridden={key in overrides}
                          >
                            <Input
                              type="number"
                              min={min}
                              max={max}
                              step={1}
                              value={draft[key]}
                              onChange={(event) => updateField(key, event.target.value)}
                              className="h-9 rounded-xl font-mono text-xs tabular-nums"
                            />
                          </FieldRow>
                        ))}
                      </div>
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
                    </div>
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
                className="rounded-xl"
                disabled={resetting || saving || !hasOverrides}
                onClick={() => void handleResetDefaults()}
              >
                <RotateCcw className="size-3.5" />
                {resetting ? 'Resetting…' : 'Reset defaults'}
              </Button>
              {error && (
                <p role="alert" className="truncate text-xs text-destructive" title={error}>
                  {error}
                </p>
              )}
              {!error && notice && (
                <p role="status" className="truncate text-xs text-muted-foreground">
                  {notice}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <DialogClose render={<Button type="button" variant="ghost" size="sm" className="rounded-xl" />}>
                Close
              </DialogClose>
              <Button
                type="submit"
                size="sm"
                className="rounded-xl"
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
      <DialogContent className="flex max-h-[85dvh] w-full flex-col gap-0 overflow-hidden rounded-2xl p-0 sm:max-w-3xl">
        <DialogHeader className="shrink-0 border-b px-5 py-4">
          <DialogTitle className="text-base font-semibold tracking-tight">Settings</DialogTitle>
          <DialogDescription>
            Manage workspace appearance, model defaults, RLM execution limits, and sandbox configuration.
          </DialogDescription>
        </DialogHeader>
        {open && <SettingsDialogBody {...props} />}
      </DialogContent>
    </Dialog>
  )
}
