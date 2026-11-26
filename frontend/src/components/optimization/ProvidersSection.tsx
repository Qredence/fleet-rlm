import {
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Pencil,
  PlugZap,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
} from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { ModelCombobox } from '@/components/optimization/ModelCombobox'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockTitle,
} from '@/components/ui/code-block'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from '@/components/ui/input-group'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  errorMessage,
  type FleetApi,
  type LLMProviderApiMode,
  type LLMProviderDialect,
  type LLMProviderModelItem,
  type LLMProviderView,
} from '@/lib/api'
import { Elevated } from '@/lib/elevated'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

interface DialectPreset {
  dialect: LLMProviderDialect
  label: string
  defaultBaseUrl: string
  defaultApiMode: LLMProviderApiMode
  requiresApiKey: boolean
  requiresBaseUrl: boolean
  supportsApiMode: boolean
  isLocal?: boolean
}

const DIALECT_PRESETS: DialectPreset[] = [
  {
    dialect: 'openai',
    label: 'OpenAI',
    defaultBaseUrl: 'https://api.openai.com/v1',
    defaultApiMode: 'auto',
    requiresApiKey: true,
    requiresBaseUrl: false,
    supportsApiMode: true,
  },
  {
    dialect: 'openai_compatible',
    label: 'OpenAI-Compatible Gateway',
    defaultBaseUrl: '',
    defaultApiMode: 'chat',
    requiresApiKey: false,
    requiresBaseUrl: true,
    supportsApiMode: true,
  },
  {
    dialect: 'anthropic',
    label: 'Anthropic',
    defaultBaseUrl: 'https://api.anthropic.com/v1',
    defaultApiMode: 'auto',
    requiresApiKey: true,
    requiresBaseUrl: false,
    supportsApiMode: false,
  },
  {
    dialect: 'gemini',
    label: 'Google Gemini',
    defaultBaseUrl: 'https://generativelanguage.googleapis.com/v1beta',
    defaultApiMode: 'auto',
    requiresApiKey: true,
    requiresBaseUrl: false,
    supportsApiMode: false,
  },
  {
    dialect: 'openrouter',
    label: 'OpenRouter',
    defaultBaseUrl: 'https://openrouter.ai/api/v1',
    defaultApiMode: 'chat',
    requiresApiKey: true,
    requiresBaseUrl: false,
    supportsApiMode: false,
  },
  {
    dialect: 'xai',
    label: 'xAI',
    defaultBaseUrl: 'https://api.x.ai/v1',
    defaultApiMode: 'auto',
    requiresApiKey: true,
    requiresBaseUrl: false,
    supportsApiMode: false,
  },
  {
    dialect: 'groq',
    label: 'Groq',
    defaultBaseUrl: 'https://api.groq.com/openai/v1',
    defaultApiMode: 'chat',
    requiresApiKey: true,
    requiresBaseUrl: false,
    supportsApiMode: false,
  },
  {
    dialect: 'deepseek',
    label: 'DeepSeek',
    defaultBaseUrl: 'https://api.deepseek.com',
    defaultApiMode: 'chat',
    requiresApiKey: true,
    requiresBaseUrl: false,
    supportsApiMode: false,
  },
  {
    dialect: 'ollama',
    label: 'Ollama (Local)',
    defaultBaseUrl: 'http://localhost:11434/v1',
    defaultApiMode: 'chat',
    requiresApiKey: false,
    requiresBaseUrl: false,
    supportsApiMode: false,
    isLocal: true,
  },
  {
    dialect: 'vllm',
    label: 'vLLM (Local / Self-hosted)',
    defaultBaseUrl: 'http://localhost:8000/v1',
    defaultApiMode: 'chat',
    requiresApiKey: false,
    requiresBaseUrl: false,
    supportsApiMode: false,
    isLocal: true,
  },
  {
    dialect: 'typesafe',
    label: 'TypeSafe (Jev)',
    defaultBaseUrl: 'https://api.typesafe.ai',
    defaultApiMode: 'auto',
    requiresApiKey: true,
    requiresBaseUrl: false,
    supportsApiMode: false,
  },
]

const DIALECT_MAP: Record<LLMProviderDialect, DialectPreset> = Object.fromEntries(
  DIALECT_PRESETS.map((preset) => [preset.dialect, preset]),
) as Record<LLMProviderDialect, DialectPreset>

const API_MODE_LABEL: Record<LLMProviderApiMode, string> = {
  auto: 'Auto',
  chat: 'Chat Completions',
  responses: 'Responses API',
}

const FERNET_KEY_SNIPPET =
  'uv run python -c "from cryptography.fernet import Fernet; print(\'FLEET_CREDENTIALS_ENCRYPTION_KEY=\' + Fernet.generate_key().decode())"'

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return 'Never tested'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

interface ProviderFormBodyProps {
  api: FleetApi
  editing: LLMProviderView | null
  encryptionConfigured: boolean
  allowPrivateEndpoints: boolean
  onCancel: () => void
  onSaved: (saved: LLMProviderView, isEdit: boolean, refreshError?: string) => void
}

function ProviderFormBody({
  api,
  editing,
  encryptionConfigured,
  allowPrivateEndpoints,
  onCancel,
  onSaved,
}: ProviderFormBodyProps) {
  const formId = useId()
  const initialDialect: LLMProviderDialect = editing?.dialect ?? 'openai'
  const initialPreset = DIALECT_MAP[initialDialect]

  const [name, setName] = useState(editing?.name ?? '')
  const [dialect, setDialect] = useState<LLMProviderDialect>(initialDialect)
  const [apiMode, setApiMode] = useState<LLMProviderApiMode>(
    editing?.api_mode ?? initialPreset.defaultApiMode,
  )
  const [baseUrl, setBaseUrl] = useState(editing?.base_url ?? initialPreset.defaultBaseUrl)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [clearSavedKey, setClearSavedKey] = useState(false)
  const [defaultModel, setDefaultModel] = useState(editing?.default_model ?? '')

  const [testedConnection, setTestedConnection] = useState<string | null>(null)
  const connection = JSON.stringify([dialect, apiMode, baseUrl.trim(), apiKey.trim(), clearSavedKey])
  const [probing, setProbing] = useState(false)
  const [probeModels, setProbeModels] = useState<LLMProviderModelItem[]>(
    editing?.cached_models ?? [],
  )
  const [probeMessage, setProbeMessage] = useState('')
  const [probeError, setProbeError] = useState('')

  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [invalidField, setInvalidField] = useState<'name' | 'base_url' | 'api_key' | null>(null)

  const preset = DIALECT_MAP[dialect]

  function handleDialectChange(nextDialect: LLMProviderDialect) {
    const prevPreset = DIALECT_MAP[dialect]
    const nextPreset = DIALECT_MAP[nextDialect]
    setDialect(nextDialect)
    setApiMode(nextPreset.defaultApiMode)
    // Auto-fill default base_url unless the user typed a custom non-default URL.
    if (!baseUrl.trim() || baseUrl.trim() === prevPreset.defaultBaseUrl) {
      setBaseUrl(nextPreset.defaultBaseUrl)
    }
    setProbeError('')
    setProbeMessage('')
    setFormError('')
    setInvalidField(null)
  }

  async function handleProbe() {
    setProbeError('')
    setProbeMessage('')
    setFormError('')

    const trimmedUrl = baseUrl.trim()
    if (preset.requiresBaseUrl && !trimmedUrl) {
      setProbeError('Enter a Base URL before testing this gateway.')
      setInvalidField('base_url')
      return
    }

    setTestedConnection(null)
    setProbing(true)
    try {
      const result = await api.probeOptimizationProvider({
        provider_id: editing && !clearSavedKey && !apiKey.trim() ? editing.id : null,
        dialect,
        api_mode: preset.supportsApiMode ? apiMode : preset.defaultApiMode,
        base_url: trimmedUrl || null,
        api_key: clearSavedKey ? null : apiKey.trim() || null,
      })
      if (!result.ok) {
        setProbeError(result.error || 'Provider endpoint test failed.')
      } else {
        setProbeModels(result.models)
        if (result.models.length) setTestedConnection(connection)
        setProbeMessage(
          `Connected — discovered ${result.count} model${result.count === 1 ? '' : 's'} from /models.`,
        )
        if (!defaultModel.trim() && result.models.length > 0) {
          setDefaultModel(result.models[0].id)
        }
      }
    } catch (failure) {
      setProbeError(errorMessage(failure))
    } finally {
      setProbing(false)
    }
  }

  async function handleSubmit() {
    setFormError('')
    setInvalidField(null)

    const trimmedName = name.trim()
    const trimmedUrl = baseUrl.trim()
    const trimmedKey = apiKey.trim()
    const trimmedModel = defaultModel.trim()

    if (!trimmedName) {
      setFormError('Enter a provider display name.')
      setInvalidField('name')
      return
    }
    if (preset.requiresBaseUrl && !trimmedUrl) {
      setFormError('Base URL is required for OpenAI-compatible gateways.')
      setInvalidField('base_url')
      return
    }
    if (trimmedKey && !encryptionConfigured) {
      setFormError(
        'Cannot save an API key while FLEET_CREDENTIALS_ENCRYPTION_KEY is unset on the backend.',
      )
      setInvalidField('api_key')
      return
    }
    if (!editing && preset.requiresApiKey && !trimmedKey) {
      setFormError(`${preset.label} requires an API key.`)
      setInvalidField('api_key')
      return
    }

    setSubmitting(true)
    try {
      const resolvedApiMode = preset.supportsApiMode ? apiMode : preset.defaultApiMode
      let saved: LLMProviderView
      if (editing) {
        const patch: {
          name: string
          dialect: LLMProviderDialect
          api_mode: LLMProviderApiMode
          base_url: string | null
          default_model: string | null
          api_key?: string | null
        } = {
          name: trimmedName,
          dialect,
          api_mode: resolvedApiMode,
          base_url: trimmedUrl || null,
          default_model: trimmedModel || null,
        }
        if (clearSavedKey) {
          patch.api_key = null
        } else if (trimmedKey) {
          patch.api_key = trimmedKey
        }
        saved = await api.updateOptimizationProvider(editing.id, patch)
      } else {
        saved = await api.createOptimizationProvider({
          name: trimmedName,
          dialect,
          api_mode: resolvedApiMode,
          base_url: trimmedUrl || null,
          api_key: trimmedKey || null,
          default_model: trimmedModel || null,
        })
      }
      let refreshError: string | undefined
      if (connection === testedConnection) {
        try {
          await api.refreshOptimizationProviderModels(saved.id)
        } catch (failure) {
          refreshError = errorMessage(failure)
        }
      }
      setApiKey('')
      onSaved(saved, Boolean(editing), refreshError)
    } catch (failure) {
      setFormError(errorMessage(failure))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <DialogHeader className="border-b px-4 py-3">
        <DialogTitle>{editing ? `Edit provider — ${editing.name}` : 'Add LLM provider'}</DialogTitle>
      </DialogHeader>

      <ScrollArea
        className="flex min-h-0 flex-1 flex-col"
        viewportClassName="min-h-0 flex-1 !h-auto p-4"
      >
        <form
          id={formId}
          onSubmit={(event) => {
            event.preventDefault()
            event.stopPropagation()
            void handleSubmit()
          }}
          className="flex flex-col gap-3.5 p-0.5"
        >
          <Field data-invalid={invalidField === 'name' || undefined}>
            <FieldLabel htmlFor={`${formId}-name`}>Provider name</FieldLabel>
            <Input
              id={`${formId}-name`}
              value={name}
              onChange={(event) => {
                setName(event.target.value)
                if (invalidField === 'name') setInvalidField(null)
              }}
              placeholder="e.g. OpenAI Prod, Local Ollama, OpenRouter"
              autoComplete="off"
            />
            <FieldDescription>
              Unique label shown in the New Run model selector and run provenance reports.
            </FieldDescription>
          </Field>

          <Field>
            <FieldLabel htmlFor={`${formId}-dialect`}>Provider preset</FieldLabel>
            <Select
              value={dialect}
              onValueChange={(value) => handleDialectChange((value || 'openai') as LLMProviderDialect)}
            >
              <SelectTrigger
                id={`${formId}-dialect`}
                aria-label="Provider dialect preset"
                placeholder="Select provider preset"
              />
              <SelectContent>
                {DIALECT_PRESETS.map((item, idx) => (
                  <SelectItem key={item.dialect} index={idx} value={item.dialect}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          {preset.supportsApiMode && (
            <Field>
              <FieldLabel htmlFor={`${formId}-api-mode`}>API dialect</FieldLabel>
              <Select
                value={apiMode}
                onValueChange={(value) => setApiMode((value || 'chat') as LLMProviderApiMode)}
              >
                <SelectTrigger
                  id={`${formId}-api-mode`}
                  aria-label="OpenAI API dialect"
                  placeholder="Select API mode"
                />
                <SelectContent>
                  <SelectItem index={0} value="chat">
                    Chat Completions (/chat/completions)
                  </SelectItem>
                  <SelectItem index={1} value="responses">
                    Responses API (/responses)
                  </SelectItem>
                  <SelectItem index={2} value="auto">
                    Auto (DSPy default)
                  </SelectItem>
                </SelectContent>
              </Select>
              <FieldDescription>
                DSPy 3.5 routes OpenAI-compatible gateways via <code>/chat/completions</code> when
                set to Chat Completions, or OpenAI&apos;s native <code>/responses</code> endpoint
                when set to Responses API.
              </FieldDescription>
            </Field>
          )}

          <Field data-invalid={invalidField === 'base_url' || undefined}>
            <FieldLabel htmlFor={`${formId}-base-url`}>
              Base URL {preset.requiresBaseUrl ? '(required)' : '(optional override)'}
            </FieldLabel>
            <Input
              id={`${formId}-base-url`}
              type="url"
              value={baseUrl}
              onChange={(event) => {
                setBaseUrl(event.target.value)
                if (invalidField === 'base_url') setInvalidField(null)
              }}
              placeholder={
                preset.defaultBaseUrl || 'https://gateway.example.com/v1'
              }
              autoComplete="off"
              spellCheck={false}
            />
            <FieldDescription>
              {preset.isLocal && !allowPrivateEndpoints
                ? 'Localhost / RFC1918 endpoints require FLEET_ALLOW_PRIVATE_LLM_ENDPOINTS=true on the backend.'
                : 'SSRF-validated before saving. Cloud metadata IPs and redirects are always blocked.'}
            </FieldDescription>
          </Field>

          <Field data-invalid={invalidField === 'api_key' || undefined}>
            <FieldLabel htmlFor={`${formId}-api-key`}>
              API key {preset.requiresApiKey ? '' : '(optional for local / keyless)'}
            </FieldLabel>
            <InputGroup>
              <InputGroupInput
                id={`${formId}-api-key`}
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                disabled={clearSavedKey || !encryptionConfigured || !window.isSecureContext}
                onChange={(event) => {
                  setApiKey(event.target.value)
                  if (invalidField === 'api_key') setInvalidField(null)
                }}
                placeholder={
                  editing?.has_api_key
                    ? `Saved (${editing.api_key_hint ?? 'encrypted'}) — leave blank to keep`
                    : preset.requiresApiKey
                      ? 'sk-...'
                      : 'Leave empty for keyless local endpoint'
                }
                autoComplete="new-password"
                spellCheck={false}
              />
              <InputGroupAddon align="inline-end">
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <InputGroupButton
                        type="button"
                        size="icon-xs"
                        disabled={clearSavedKey || !encryptionConfigured || !window.isSecureContext}
                        aria-label={showKey ? 'Hide API key' : 'Show API key'}
                        onClick={() => setShowKey((prev) => !prev)}
                      />
                    }
                  >
                    {showKey ? (
                      <EyeOff className="size-3.5" aria-hidden="true" />
                    ) : (
                      <Eye className="size-3.5" aria-hidden="true" />
                    )}
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    {showKey ? 'Hide API key' : 'Show API key'}
                  </TooltipContent>
                </Tooltip>
              </InputGroupAddon>
            </InputGroup>
            <FieldDescription>
              Write-only: encrypted on the host with Fernet and never returned to the browser.
            </FieldDescription>
          </Field>

          {editing?.has_api_key && (
            <div className="rounded-lg border px-2 py-1">
              <Switch
                size="compact"
                label="Clear saved API key"
                checked={clearSavedKey}
                onToggle={() => {
                  setClearSavedKey((prev) => !prev)
                  setApiKey('')
                }}
              />
            </div>
          )}

          {!encryptionConfigured && (preset.requiresApiKey || apiKey.trim().length > 0) && (
            <Alert variant="destructive">
              <AlertDescription>
                Set <code>FLEET_CREDENTIALS_ENCRYPTION_KEY</code> in the backend environment to
                store encrypted API keys. Keyless local endpoints (Ollama, vLLM) work without it.
              </AlertDescription>
            </Alert>
          )}

          {/* Live probe & model discovery */}
          <div className="flex flex-col gap-2.5 rounded-lg border bg-muted/20 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <p
                  className={`${typeClass('body')} text-foreground`}
                  style={{ fontVariationSettings: fontWeights.semibold }}
                >
                  Connection &amp; model discovery
                </p>
                <p className={`${typeClass('caption')} text-muted-foreground`}>
                  Verify credentials and populate models directly from <code>/models</code>.
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={probing || submitting}
                onClick={() => void handleProbe()}
                data-testid="probe-provider-button"
              >
                <PlugZap className="size-3.5" aria-hidden="true" />
                {probing ? 'Testing…' : 'Test & Fetch /models'}
              </Button>
            </div>

            {probeMessage && (
              <div
                role="status"
                className="flex flex-col gap-1.5 rounded-md border border-border/80 bg-card p-2.5"
              >
                <div className={`${typeClass('caption')} flex items-center gap-1.5 text-foreground`}>
                  <CheckCircle2 className="size-3.5 shrink-0 text-success" aria-hidden="true" />
                  <span>{probeMessage}</span>
                </div>
                {probeModels.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {probeModels.slice(0, 6).map((item) => (
                      <Badge key={item.id} variant="secondary" className="font-mono text-[11px]">
                        {item.id}
                      </Badge>
                    ))}
                    {probeModels.length > 6 && (
                      <Badge variant="outline" className="text-[11px]">
                        +{probeModels.length - 6} more
                      </Badge>
                    )}
                  </div>
                )}
              </div>
            )}

            {probeError && (
              <Alert variant="destructive">
                <AlertDescription>{probeError}</AlertDescription>
              </Alert>
            )}

            <Field>
              <FieldLabel htmlFor={`${formId}-default-model`}>Default model (optional)</FieldLabel>
              <ModelCombobox
                id={`${formId}-default-model`}
                ariaLabel="Default provider model"
                value={defaultModel}
                onChange={setDefaultModel}
                models={probeModels}
                loading={probing}
                onRefresh={() => void handleProbe()}
                placeholder="Select a fetched model or type any model ID…"
                emptyHint="Click 'Test & Fetch /models' or type a custom model ID."
              />
            </Field>
          </div>

          {formError && (
            <Alert variant="destructive">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          )}
        </form>
      </ScrollArea>

      <DialogFooter className="mx-0 mb-0 border-t px-4 py-3">
        <Button type="button" variant="outline" disabled={submitting} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" form={formId} disabled={submitting} data-testid="save-provider-button">
          {submitting ? 'Saving…' : editing ? 'Save provider' : 'Add provider'}
        </Button>
      </DialogFooter>
    </>
  )
}

function ProviderEntry({ rows, ...props }: React.ComponentProps<'div'> & { rows: boolean }) {
  return rows ? <div {...props} /> : <Elevated offset={1} shadowLevel={2} {...props} />
}

export function ProvidersSection({
  loadError,
  onRetry,
  api,
  providers,
  encryptionConfigured,
  allowPrivateEndpoints,
  loading,
  onChanged,
  onOpenModels,
  layout = 'cards',
}: {
  api: FleetApi
  loadError: string | null
  onRetry: () => void
  providers: LLMProviderView[] | null
  encryptionConfigured: boolean
  allowPrivateEndpoints: boolean
  loading: boolean
  onOpenModels?: () => void
  layout?: 'cards' | 'rows'
  onChanged: (announcement?: string) => Promise<void> | void
}) {
  const rows = layout === 'rows'
  const providerListClass = rows
    ? '@container min-w-0 divide-y divide-border/60'
    : 'grid min-w-0 grid-cols-[repeat(auto-fill,minmax(min(100%,24rem),1fr))] gap-4'
  const providerEntryClass = rows
    ? 'flex min-w-0 flex-col gap-3 py-4 @lg:flex-row @lg:items-center @lg:justify-between'
    : 'flex min-w-0 flex-col gap-3 rounded-xl p-4 ring-1 ring-foreground/10'
  const editorTrigger = useRef<HTMLButtonElement | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogSession, setDialogSession] = useState(0)
  const [editingProvider, setEditingProvider] = useState<LLMProviderView | null>(null)

  const [refreshingId, setRefreshingId] = useState<string | null>(null)
  const [cardNotice, setCardNotice] = useState<Record<string, { ok: boolean; text: string }>>({})

  const [deleteTarget, setDeleteTarget] = useState<LLMProviderView | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')

  function openCreateDialog(event: React.MouseEvent<HTMLButtonElement>) {
    editorTrigger.current = event.currentTarget
    setEditingProvider(null)
    setDialogSession((prev) => prev + 1)
    setDialogOpen(true)
  }

  function openEditDialog(provider: LLMProviderView, event: React.MouseEvent<HTMLButtonElement>) {
    editorTrigger.current = event.currentTarget
    setEditingProvider(provider)
    setDialogSession((prev) => prev + 1)
    setDialogOpen(true)
  }

  async function handleRefreshModels(provider: LLMProviderView) {
    setRefreshingId(provider.id)
    setCardNotice((prev) => {
      const next = { ...prev }
      delete next[provider.id]
      return next
    })
    try {
      const result = await api.refreshOptimizationProviderModels(provider.id)
      setCardNotice((prev) => ({
        ...prev,
        [provider.id]: {
          ok: true,
          text: `Refreshed ${result.count} model${result.count === 1 ? '' : 's'} from /models.`,
        },
      }))
      await onChanged(`Refreshed ${result.count} models for ${provider.name}.`)
    } catch (failure) {
      setCardNotice((prev) => ({
        ...prev,
        [provider.id]: { ok: false, text: errorMessage(failure) },
      }))
    } finally {
      setRefreshingId(null)
    }
  }

  return (
    <div className="flex flex-col gap-5" data-testid="providers-section">
      {/* Section Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className={typeClass('title')} style={{ fontVariationSettings: fontWeights.semibold }}>
            LLM Providers
          </h2>
          <p className={`${typeClass('body')} text-muted-foreground`}>
            Register API endpoints and fetch live <code>/models</code> catalogs for chat and optimization.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={allowPrivateEndpoints ? 'secondary' : 'outline'}>
            {allowPrivateEndpoints ? 'Private/local endpoints allowed' : 'Public HTTPS endpoints only'}
          </Badge>
          <Button
            type="button"
            size="sm"
            onClick={openCreateDialog}
            data-testid="add-provider-button"
          >
            <Plus className="size-3.5" aria-hidden="true" />
            Add provider
          </Button>
        </div>
      </div>

      {/* Encryption key warning banner */}
      {!encryptionConfigured && (
        <div
          className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4"
          role="region"
          aria-label="Provider credential encryption status"
          data-testid="encryption-warning-banner"
        >
          <div className="flex items-start gap-2.5">
            <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
            <div className="flex min-w-0 flex-col gap-1">
              <p
                className={`${typeClass('subtitle')} text-foreground`}
                style={{ fontVariationSettings: fontWeights.semibold }}
              >
                FLEET_CREDENTIALS_ENCRYPTION_KEY is not configured
              </p>
              <p className={`${typeClass('body')} text-muted-foreground`}>
                Providers that require API keys cannot save credentials until a host encryption key
                is added to your backend <code>.env</code>. Keyless local endpoints such as Ollama
                and vLLM still work without it.
              </p>
            </div>
          </div>
          <CodeBlock size="sm">
            <CodeBlockHeader>
              <CodeBlockTitle>Generate a Fernet encryption key (run in backend/)</CodeBlockTitle>
              <CodeBlockActions>
                <Tooltip>
                  <TooltipTrigger render={<span className="inline-flex" />}>
                    <CodeBlockCopyButton
                      value={FERNET_KEY_SNIPPET}
                      aria-label="Copy Fernet key command"
                    />
                  </TooltipTrigger>
                  <TooltipContent side="top">Copy key generator command</TooltipContent>
                </Tooltip>
              </CodeBlockActions>
            </CodeBlockHeader>
            <CodeBlockContent wrap>{FERNET_KEY_SNIPPET}</CodeBlockContent>
          </CodeBlock>
        </div>
      )}

      {loadError !== null ? (
        <Alert variant="destructive">
          <AlertDescription>Unable to load providers. {loadError}</AlertDescription>
          <Button size="sm" variant="outline" onClick={onRetry}>Retry</Button>
        </Alert>
      ) : loading && !providers ? (
        <div className={providerListClass} role="status" aria-label="Loading providers">
          {[0, 1, 2].map((idx) => (
            <ProviderEntry
              key={idx}
              rows={rows}
              className={providerEntryClass}
            >
              <div className="grid min-w-0 flex-1 gap-2">
                <Skeleton className="h-5 w-36 bg-hover motion-reduce:animate-none" />
                <Skeleton className="h-4 w-full max-w-64 bg-hover motion-reduce:animate-none" />
              </div>
              <Skeleton className="h-7 w-40 bg-hover motion-reduce:animate-none" />
            </ProviderEntry>
          ))}
        </div>
      ) : !providers || providers.length === 0 ? (
        <div className={rows ? "flex flex-col items-start gap-3 py-4" : "flex flex-col items-start gap-3 rounded-xl border bg-card p-5"}>
          <div className="flex items-center gap-2">
            <KeyRound className="size-4 text-muted-foreground" aria-hidden="true" />
            <h3 className={typeClass('subtitle')} style={{ fontVariationSettings: fontWeights.semibold }}>
              No custom LLM providers registered yet
            </h3>
          </div>
          <p className={`${typeClass('body')} max-w-2xl text-muted-foreground`}>
            Runs currently fall back to your workspace <code>.env</code> models. Add OpenAI,
            Anthropic, Gemini, OpenRouter, TypeSafe (Jev), or a local Ollama/vLLM endpoint to mix
            providers per optimizer role.
          </p>
          <Button type="button" size="sm" onClick={openCreateDialog}>
            <Plus className="size-3.5" aria-hidden="true" />
            Add first provider
          </Button>
        </div>
      ) : (
        <div className={providerListClass} role="region" aria-label="Registered LLM providers">
          {providers.map((provider) => {
            const preset = DIALECT_MAP[provider.dialect]
            const isRefreshing = refreshingId === provider.id
            const notice = cardNotice[provider.id]
            const showModeBadge =
              provider.dialect === 'openai' || provider.dialect === 'openai_compatible'

            return (
              <ProviderEntry
                key={provider.id}
                rows={rows}
                data-testid={`provider-${rows ? "row" : "card"}-${provider.id}`}
                className={providerEntryClass}
              >
                <div className="flex min-w-0 flex-col gap-3">
                  <div className="flex min-w-0 flex-col items-start gap-2">
                    <div className="min-w-0">
                      <h3
                        className={`${typeClass('subtitle')} [overflow-wrap:anywhere] text-foreground`}
                        style={{ fontVariationSettings: fontWeights.semibold }}
                        title={provider.name}
                      >
                        {provider.name}
                      </h3>
                      <p className={`${typeClass('caption')} mt-0.5 [overflow-wrap:anywhere] font-mono text-muted-foreground`}>
                        {provider.base_url || preset?.defaultBaseUrl || 'Provider default endpoint'}
                      </p>
                    </div>
                    <div className="flex max-w-full flex-wrap gap-1">
                      <Badge variant="secondary">{preset?.label ?? provider.dialect}</Badge>
                      {showModeBadge && (
                        <Badge variant="outline">{API_MODE_LABEL[provider.api_mode]}</Badge>
                      )}
                    </div>
                  </div>

                  <dl className={rows ? "flex flex-wrap gap-x-4 gap-y-2" : "grid grid-cols-[repeat(auto-fit,minmax(min(100%,9rem),1fr))] gap-2 border-t border-border/60 pt-2.5"}>
                    <div className="min-w-0">
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>API key</dt>
                      <dd className={`${typeClass('body')} [overflow-wrap:anywhere] font-mono text-foreground`}>
                        {provider.has_api_key ? (provider.api_key_hint ?? 'Saved') : 'No API key'}
                      </dd>
                    </div>
                    <div className="min-w-0">
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>Default model</dt>
                      <dd
                        className={`${typeClass('body')} [overflow-wrap:anywhere] font-mono text-foreground`}
                        title={provider.default_model ?? undefined}
                      >
                        {provider.default_model || '—'}
                      </dd>
                    </div>
                  </dl>

                  <div className={rows ? "flex min-w-0 flex-col gap-1.5" : "flex min-w-0 flex-col gap-1.5 border-t border-border/60 pt-2.5"}>
                    <p className={`${typeClass('caption')} tabular-nums text-muted-foreground`}>
                      {provider.cached_models.length} cached model
                      {provider.cached_models.length === 1 ? '' : 's'} ·{' '}
                      {formatTimestamp(provider.last_tested_at)}
                    </p>
                    {notice && (
                      <p
                        role={notice.ok ? 'status' : 'alert'}
                        className={`${typeClass('caption')} [overflow-wrap:anywhere] ${notice.ok ? 'text-foreground' : 'text-destructive'}`}
                      >
                        {notice.text}
                      </p>
                    )}
                  </div>
                </div>
                <div className={rows ? "flex shrink-0 flex-wrap items-center gap-2 @lg:justify-end" : "mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2.5"}>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={isRefreshing}
                    onClick={() => void handleRefreshModels(provider)}
                    data-testid={`refresh-provider-${provider.id}`}
                  >
                    <RefreshCw
                      className={`size-3.5 ${isRefreshing ? 'animate-spin motion-reduce:animate-none' : ''}`}
                      aria-hidden="true"
                    />
                    {isRefreshing ? 'Fetching…' : 'Fetch /models'}
                  </Button>

                  <div className="flex flex-wrap items-center gap-1">
                    {onOpenModels && (provider.default_model || provider.cached_models.length > 0) && (
                      <Button type="button" variant="ghost" size="sm" onClick={onOpenModels}>
                        Open Models
                      </Button>
                    )}
                    <Tooltip>
                      <TooltipTrigger
                        render={
                          <Button
                            type="button"
                            size="icon-sm"
                            variant="ghost"
                            aria-label={`Edit provider ${provider.name}`}
                            onClick={(event) => openEditDialog(provider, event)}
                            data-testid={`edit-provider-${provider.id}`}
                          />
                        }
                      >
                        <Pencil className="size-3.5" aria-hidden="true" />
                      </TooltipTrigger>
                      <TooltipContent side="top">Edit provider</TooltipContent>
                    </Tooltip>

                    <Tooltip>
                      <TooltipTrigger
                        render={
                          <Button
                            type="button"
                            size="icon-sm"
                            variant="ghost"
                            aria-label={`Delete provider ${provider.name}`}
                            onClick={() => {
                              setDeleteError('')
                              setDeleteTarget(provider)
                            }}
                            data-testid={`delete-provider-${provider.id}`}
                          />
                        }
                      >
                        <Trash2 className="size-3.5" aria-hidden="true" />
                      </TooltipTrigger>
                      <TooltipContent side="top">Delete provider</TooltipContent>
                    </Tooltip>
                  </div>
                </div>
              </ProviderEntry>
            )
          })}
        </div>
      )}

      {/* Add / Edit Provider Dialog (remounted per open session) */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent finalFocus={editorTrigger} className="flex max-h-[min(760px,calc(100dvh-4rem))] flex-col gap-0 p-0 sm:max-w-lg">
          {dialogOpen && (
            <ProviderFormBody
              key={`${dialogSession}:${editingProvider?.id ?? 'new'}`}
              api={api}
              editing={editingProvider}
              encryptionConfigured={encryptionConfigured}
              allowPrivateEndpoints={allowPrivateEndpoints}
              onCancel={() => setDialogOpen(false)}
              onSaved={(saved, isEdit, refreshError) => {
                if (refreshError) {
                  setCardNotice(current => ({
                    ...current, [saved.id]: { ok: false, text: `Provider saved. Could not fetch models. ${refreshError}` },
                  }))
                }
                setDialogOpen(false)
                void onChanged(
                  isEdit ? `Updated provider ${saved.name}.` : `Added provider ${saved.name}.`,
                )
              }}
            />
          )}
        </DialogContent>
      </Dialog>

      {/* Delete Provider AlertDialog with in-modal 409 error display */}
      <AlertDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => {
          if (!open && !deleting) {
            setDeleteTarget(null)
            setDeleteError('')
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete LLM provider?</AlertDialogTitle>
            <AlertDialogDescription>
              Permanently removes <code>{deleteTarget?.name}</code> and its encrypted credentials.
              Providers pinned to an active promotion or currently running job cannot be deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>

          {deleteError && (
            <Alert variant="destructive">
              <AlertDescription>{deleteError}</AlertDescription>
            </Alert>
          )}

          <AlertDialogFooter>
            {onOpenModels && deleteError.includes('workspace defaults') && (
              <Button type="button" variant="outline" disabled={deleting} onClick={() => {
                setDeleteTarget(null)
                onOpenModels()
              }}>Open Models</Button>
            )}
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting}
              onClick={async () => {
                if (!deleteTarget) return
                setDeleteError('')
                setDeleting(true)
                try {
                  const targetName = deleteTarget.name
                  await api.deleteOptimizationProvider(deleteTarget.id)
                  setDeleteTarget(null)
                  await onChanged(`Deleted provider ${targetName}.`)
                } catch (failure) {
                  setDeleteError(errorMessage(failure))
                } finally {
                  setDeleting(false)
                }
              }}
            >
              {deleting ? 'Deleting…' : 'Delete provider'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
