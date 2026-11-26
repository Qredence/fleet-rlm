import { AlertTriangle, ChevronDown, Eye, EyeOff, KeyRound, Play } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { ModelCombobox } from '@/components/optimization/ModelCombobox'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
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
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  errorMessage,
  type FleetApi,
  type LLMProviderModelItem,
  type LLMProviderView,
  type LMRoleSelection,
  type OptimizationBranchMode,
  type OptimizationCapabilities,
  type OptimizationDatasetSummary,
  type OptimizationJobCreate,
  type OptimizationJobDetail,
  type OptimizationTarget,
} from '@/lib/api'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

export interface LaunchDefaults {
  target?: OptimizationTarget
  datasetId?: string
  programKind?: 'rlm' | 'flex'
  seed?: number
  maxMetricCalls?: number
  holdoutRepeats?: number
  rootLm?: LMRoleSelection | null
  subLm?: LMRoleSelection | null
  reflectionLm?: LMRoleSelection | null
  judgeLm?: LMRoleSelection | null
  decisionLm?: LMRoleSelection | null
}

const WORKSPACE_PROVIDER_VALUE = '__workspace__'

const TARGET_EXPLANATION: Record<OptimizationTarget, string> = {
  skills:
    'Calibrates per-turn skill routing thresholds and option weights via 5-fold ReAnchor CV to reduce unnecessary skill loads without missing relevant skills.',
  memory:
    'Calibrates consolidation save, action, and scope gates via ReAnchor CV to cut false-positive memory saves while keeping useful session memories.',
  rlm: 'Evolves Root RLM prompt instructions with GEPA against holdout tasks and verifies zero blocking failures or paired regressions before saving.',
}

interface RoleState {
  providerId: string
  model: string
}

function toInitialRoleState(selection?: LMRoleSelection | null): RoleState {
  return {
    providerId: selection?.provider_id ?? WORKSPACE_PROVIDER_VALUE,
    model: selection?.model ?? '',
  }
}

function toPayloadRoleSelection(state: RoleState): LMRoleSelection | undefined {
  const providerId = state.providerId !== WORKSPACE_PROVIDER_VALUE ? state.providerId : null
  const model = state.model.trim() || null
  if (!providerId && !model) return undefined
  return { provider_id: providerId, model }
}

function storageLabelForDataset(dataset: OptimizationDatasetSummary): string {
  const backend = dataset.storage_backend ?? (dataset.source === 'builtin' ? 'builtin' : 'neon')
  if (backend === 'daytona_volume') return 'Daytona Volume'
  if (backend === 'builtin') return 'Built-in'
  return 'Neon'
}

function defaultBranchForCapabilities(
  capabilities: OptimizationCapabilities | null,
): OptimizationBranchMode {
  if (capabilities?.auto_neon_enabled) return 'auto_neon'
  if (capabilities?.configured_branch_enabled) return 'configured_branch'
  return 'custom_branch'
}

function RoleModelPicker({
  idPrefix,
  label,
  description,
  value,
  onChange,
  providers,
  workspaceDefaultModel,
  refreshingProviderId,
  refreshError,
  onRefreshProvider,
}: {
  idPrefix: string
  label: string
  description: string
  value: RoleState
  onChange: (next: RoleState) => void
  providers: LLMProviderView[]
  workspaceDefaultModel: string | null
  refreshingProviderId: string | null
  refreshError?: string
  onRefreshProvider: (providerId: string) => void
}) {
  const selectedProvider =
    value.providerId !== WORKSPACE_PROVIDER_VALUE
      ? (providers.find((item) => item.id === value.providerId) ?? null)
      : null

  const comboboxModels = useMemo<LLMProviderModelItem[]>(() => {
    if (selectedProvider) {
      const list = [...selectedProvider.cached_models]
      if (
        selectedProvider.default_model &&
        !list.some((item) => item.id === selectedProvider.default_model)
      ) {
        list.unshift({ id: selectedProvider.default_model, label: selectedProvider.default_model })
      }
      return list
    }
    if (workspaceDefaultModel) {
      return [{ id: workspaceDefaultModel, label: `${workspaceDefaultModel} (workspace default)` }]
    }
    return []
  }, [selectedProvider, workspaceDefaultModel])

  return (
    <Field>
      <FieldLabel htmlFor={`${idPrefix}-model`}>{label}</FieldLabel>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)]">
        <Select
          size="compact"
          value={value.providerId}
          onValueChange={(nextProviderId) => {
            const resolvedId = nextProviderId || WORKSPACE_PROVIDER_VALUE
            const nextProvider =
              resolvedId !== WORKSPACE_PROVIDER_VALUE
                ? (providers.find((item) => item.id === resolvedId) ?? null)
                : null
            onChange({
              providerId: resolvedId,
              model: nextProvider?.default_model ?? '',
            })
          }}
        >
          <SelectTrigger
            id={`${idPrefix}-provider`}
            aria-label={`${label} provider`}
            placeholder="Workspace default (.env)"
          />
          <SelectContent>
            <SelectItem index={0} value={WORKSPACE_PROVIDER_VALUE}>
              Workspace default (.env)
            </SelectItem>
            {providers.map((provider, idx) => (
              <SelectItem key={provider.id} index={idx + 1} value={provider.id}>
                {provider.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <ModelCombobox
          id={`${idPrefix}-model`}
          ariaLabel={`${label} model`}
          value={value.model}
          onChange={(modelId) => onChange({ ...value, model: modelId })}
          models={comboboxModels}
          loading={selectedProvider !== null && refreshingProviderId === selectedProvider.id}
          onRefresh={
            selectedProvider ? () => onRefreshProvider(selectedProvider.id) : undefined
          }
          placeholder={
            selectedProvider
              ? selectedProvider.default_model || 'Choose or type model ID…'
              : workspaceDefaultModel
                ? `Default (${workspaceDefaultModel})`
                : 'Workspace default'
          }
          emptyHint={
            selectedProvider
              ? 'Click Refresh /models or type any model ID.'
              : 'Leave empty to inherit workspace .env default, or type an override.'
          }
        />
      </div>
      <FieldDescription>{description}</FieldDescription>
      {refreshError && (
        <p role="alert" className={`${typeClass('caption')} text-destructive`}>
          {refreshError}
        </p>
      )}
    </Field>
  )
}

interface LaunchOptimizationFormBodyProps {
  api: FleetApi
  capabilities: OptimizationCapabilities | null
  datasets: OptimizationDatasetSummary[]
  providers: LLMProviderView[]
  defaults: LaunchDefaults
  onClose: () => void
  onStarted: (job: OptimizationJobDetail) => void
  onManageProviders?: () => void
  onProviderRefreshed?: (provider: LLMProviderView) => void
}

export function LaunchOptimizationFormBody({
  api,
  capabilities,
  datasets,
  providers: initialProviders,
  defaults,
  onClose,
  onStarted,
  onManageProviders,
  onProviderRefreshed,
}: LaunchOptimizationFormBodyProps) {
  const formId = useId()
  const initialTarget: OptimizationTarget = defaults.target ?? 'skills'
  const initialTargetDatasets = datasets.filter((item) => item.target === initialTarget)
  const initialDatasetId =
    defaults.datasetId &&
    (defaults.datasetId === 'db' ||
      initialTargetDatasets.some((item) => item.id === defaults.datasetId))
      ? defaults.datasetId
      : (initialTargetDatasets[0]?.id ?? (initialTarget === 'rlm' ? 'db' : ''))

  const [target, setTarget] = useState<OptimizationTarget>(initialTarget)
  const [datasetId, setDatasetId] = useState<string>(initialDatasetId)
  const [programKind, setProgramKind] = useState<'rlm' | 'flex'>(defaults.programKind ?? 'rlm')
  const [branchMode, setBranchMode] = useState<OptimizationBranchMode>(() =>
    defaultBranchForCapabilities(capabilities),
  )
  const [customDbUrl, setCustomDbUrl] = useState('')
  const [showCustomUrl, setShowCustomUrl] = useState(false)
  const [allowDirectOptIn, setAllowDirectOptIn] = useState(false)

  const [localProviders, setLocalProviders] = useState<LLMProviderView[]>(initialProviders)
  const [refreshingProviderId, setRefreshingProviderId] = useState<string | null>(null)
  const [providerRefreshError, setProviderRefreshError] = useState<Record<string, string>>({})

  const [rootLm, setRootLm] = useState<RoleState>(() => toInitialRoleState(defaults.rootLm))
  const [subLm, setSubLm] = useState<RoleState>(() => toInitialRoleState(defaults.subLm))
  const [reflectionLm, setReflectionLm] = useState<RoleState>(() =>
    toInitialRoleState(defaults.reflectionLm),
  )
  const [judgeLm, setJudgeLm] = useState<RoleState>(() => toInitialRoleState(defaults.judgeLm))
  const [decisionLm, setDecisionLm] = useState<RoleState>(() =>
    toInitialRoleState(defaults.decisionLm),
  )

  const [maxMetricCalls, setMaxMetricCalls] = useState(String(defaults.maxMetricCalls ?? 80))
  const [holdoutRepeats, setHoldoutRepeats] = useState(String(defaults.holdoutRepeats ?? 3))
  const [trainRatio, setTrainRatio] = useState('')
  const [seed, setSeed] = useState(String(defaults.seed ?? 0))
  const [disableCache, setDisableCache] = useState(true)
  const [localInterpreter, setLocalInterpreter] = useState(false)
  const [force, setForce] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(
    Boolean(defaults.subLm?.provider_id || defaults.subLm?.model || defaults.judgeLm?.provider_id || defaults.judgeLm?.model),
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [invalidField, setInvalidField] = useState<
    'custom_db_url' | 'seed' | 'train_ratio' | 'max_metric_calls' | 'holdout_repeats' | null
  >(null)

  const matchingDatasets = useMemo(
    () => datasets.filter((item) => item.target === target),
    [datasets, target],
  )

  const selectedDecisionProvider =
    decisionLm.providerId !== WORKSPACE_PROVIDER_VALUE
      ? (localProviders.find((item) => item.id === decisionLm.providerId) ?? null)
      : null
  const isNonTypeSafeDecisionModel = useMemo(() => {
    if (target === 'rlm') return false
    if (selectedDecisionProvider) {
      return selectedDecisionProvider.dialect !== 'typesafe'
    }
    const explicitModel = decisionLm.model.trim()
    if (explicitModel) {
      return !explicitModel.toLowerCase().startsWith('typesafe:')
    }
    const wsModel = capabilities?.decision_model ?? ''
    return Boolean(wsModel) && !wsModel.toLowerCase().startsWith('typesafe:')
  }, [capabilities?.decision_model, decisionLm.model, selectedDecisionProvider, target])

  const handleTargetChange = (nextTarget: OptimizationTarget) => {
    setTarget(nextTarget)
    const forTarget = datasets.filter((item) => item.target === nextTarget)
    const nextDataset = forTarget[0]?.id ?? (nextTarget === 'rlm' ? 'db' : '')
    setDatasetId(nextDataset)
    if (nextDataset === 'db' && branchMode === 'direct_readonly') {
      setBranchMode(defaultBranchForCapabilities(capabilities))
      setAllowDirectOptIn(false)
    }
    setError('')
    setInvalidField(null)
  }

  const handleDatasetChange = (nextDatasetId: string) => {
    setDatasetId(nextDatasetId)
    if (nextDatasetId === 'db' && branchMode === 'direct_readonly') {
      setBranchMode(defaultBranchForCapabilities(capabilities))
      setAllowDirectOptIn(false)
    }
    setError('')
  }

  async function handleRefreshProvider(providerId: string) {
    setRefreshingProviderId(providerId)
    setProviderRefreshError((prev) => {
      const next = { ...prev }
      delete next[providerId]
      return next
    })
    try {
      const result = await api.refreshOptimizationProviderModels(providerId)
      setLocalProviders((prev) =>
        prev.map((item) => (item.id === result.provider.id ? result.provider : item)),
      )
      onProviderRefreshed?.(result.provider)
    } catch (failure) {
      setProviderRefreshError((prev) => ({
        ...prev,
        [providerId]: errorMessage(failure),
      }))
    } finally {
      setRefreshingProviderId(null)
    }
  }

  async function submitForm() {
    setError('')
    setInvalidField(null)

    const parsedSeed = Number.parseInt(seed, 10)
    if (Number.isNaN(parsedSeed) || parsedSeed < 0) {
      setError('Random seed must be a non-negative integer.')
      setInvalidField('seed')
      return
    }
    if (branchMode === 'custom_branch' && !customDbUrl.trim()) {
      setError('Enter an isolated Neon branch PostgreSQL URL.')
      setInvalidField('custom_db_url')
      return
    }
    if (branchMode === 'direct_readonly' && !allowDirectOptIn) {
      setError('Confirm read-only JSONL opt-in before targeting the primary DB.')
      return
    }

    const payload: OptimizationJobCreate = {
      target,
      dataset_id: datasetId || null,
      branch_mode: branchMode,
      seed: parsedSeed,
      force,
    }
    if (branchMode === 'custom_branch') payload.custom_database_url = customDbUrl.trim()
    if (branchMode === 'direct_readonly') payload.allow_direct_opt_in = true
    if (trainRatio.trim()) {
      const ratio = Number.parseFloat(trainRatio)
      if (Number.isNaN(ratio) || ratio <= 0 || ratio >= 1) {
        setError('Train ratio must be strictly between 0 and 1.')
        setInvalidField('train_ratio')
        return
      }
      payload.train_ratio = ratio
    }
    if (target === 'rlm') {
      const budget = Number.parseInt(maxMetricCalls, 10)
      const repeats = Number.parseInt(holdoutRepeats, 10)
      if (Number.isNaN(budget) || budget < 1) {
        setError('GEPA max metric calls must be a positive integer.')
        setInvalidField('max_metric_calls')
        return
      }
      if (Number.isNaN(repeats) || repeats < 1) {
        setError('Holdout repeats must be a positive integer.')
        setInvalidField('holdout_repeats')
        return
      }
      payload.program_kind = programKind
      payload.max_metric_calls = budget
      payload.holdout_repeats = repeats
      payload.disable_cache = disableCache
      if (capabilities?.allow_local_interpreter === true) {
        payload.local_interpreter = localInterpreter
      }
      const rootConfig = toPayloadRoleSelection(rootLm)
      const subConfig = toPayloadRoleSelection(subLm)
      const reflectionConfig = toPayloadRoleSelection(reflectionLm)
      const judgeConfig = toPayloadRoleSelection(judgeLm)
      if (rootConfig) payload.root_lm = rootConfig
      if (subConfig) payload.sub_lm = subConfig
      if (reflectionConfig) payload.reflection_lm_config = reflectionConfig
      if (judgeConfig) payload.judge_lm_config = judgeConfig
    } else {
      const decisionConfig = toPayloadRoleSelection(decisionLm)
      if (decisionConfig) payload.decision_lm = decisionConfig
    }

    setSubmitting(true)
    try {
      const started = await api.createOptimizationJob(payload)
      onClose()
      onStarted(started)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <DialogHeader className="border-b px-4 py-3">
        <DialogTitle>New Optimization Run</DialogTitle>
      </DialogHeader>

      <ScrollArea
        className="flex min-h-0 flex-1 flex-col"
        viewportClassName="min-h-0 flex-1 !h-auto p-4"
      >
        <form
          id={formId}
          onSubmit={(event) => {
            event.preventDefault()
            void submitForm()
          }}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              event.preventDefault()
              void submitForm()
            }
          }}
          className="flex flex-col gap-3.5 p-0.5"
        >
          <Field>
            <FieldLabel htmlFor={`${formId}-target`}>Target</FieldLabel>
            <Select
              value={target}
              onValueChange={(value) => handleTargetChange((value || 'skills') as OptimizationTarget)}
            >
              <SelectTrigger
                id={`${formId}-target`}
                aria-label="Optimization target"
                placeholder="Select target"
              />
              <SelectContent>
                <SelectItem index={0} value="skills">
                  Skill Selector (ReAnchor)
                </SelectItem>
                <SelectItem index={1} value="memory">
                  Memory Gate (ReAnchor)
                </SelectItem>
                <SelectItem index={2} value="rlm">
                  Root RLM Program (GEPA)
                </SelectItem>
              </SelectContent>
            </Select>
            <FieldDescription>{TARGET_EXPLANATION[target]}</FieldDescription>
          </Field>

          <Field>
            <FieldLabel htmlFor={`${formId}-dataset`}>Dataset</FieldLabel>
            <Select value={datasetId} onValueChange={handleDatasetChange}>
              <SelectTrigger
                id={`${formId}-dataset`}
                aria-label="Evaluation dataset"
                placeholder="Choose dataset"
              />
              <SelectContent>
                {matchingDatasets.map((item, idx) => (
                  <SelectItem key={item.id} index={idx} value={item.id}>
                    {`${item.name} (${item.row_count} rows · ${storageLabelForDataset(item)})`}
                  </SelectItem>
                ))}
                {target === 'rlm' && (
                  <SelectItem index={matchingDatasets.length} value="db">
                    Past Completed Neon Runs (DB fallback)
                  </SelectItem>
                )}
              </SelectContent>
            </Select>
            <FieldDescription>
              Evaluation rows are split by lineage into train/val/holdout so no conversation leaks
              into the holdout gate.
            </FieldDescription>
          </Field>

          <Field>
            <FieldLabel htmlFor={`${formId}-branch-mode`}>Neon database isolation</FieldLabel>
            <Select
              value={branchMode}
              onValueChange={(value) => {
                setBranchMode((value || 'configured_branch') as OptimizationBranchMode)
                setError('')
                setInvalidField(null)
              }}
            >
              <SelectTrigger
                id={`${formId}-branch-mode`}
                aria-label="Neon database isolation"
                placeholder="Select branch mode"
              />
              <SelectContent>
                <SelectItem index={0} value="auto_neon" disabled={!capabilities?.auto_neon_enabled}>
                  Auto ephemeral Neon branch
                </SelectItem>
                <SelectItem
                  index={1}
                  value="configured_branch"
                  disabled={!capabilities?.configured_branch_enabled}
                >
                  {capabilities?.configured_branch_label
                    ? `Configured branch (${capabilities.configured_branch_label})`
                    : 'Configured optimization branch'}
                </SelectItem>
                <SelectItem index={2} value="custom_branch">
                  Custom Neon branch URL
                </SelectItem>
                <SelectItem index={3} value="direct_readonly" disabled={datasetId === 'db'}>
                  Primary DB (read-only JSONL only)
                </SelectItem>
              </SelectContent>
            </Select>
            <FieldDescription>
              Offline optimization runs stay isolated from your primary serving database.
            </FieldDescription>
          </Field>

          {branchMode === 'custom_branch' && (
            <Field data-invalid={invalidField === 'custom_db_url' || undefined}>
              <FieldLabel htmlFor={`${formId}-custom-branch-url`}>
                Isolated Neon branch URL
              </FieldLabel>
              <InputGroup>
                <InputGroupInput
                  id={`${formId}-custom-branch-url`}
                  type={showCustomUrl ? 'text' : 'password'}
                  value={customDbUrl}
                  onChange={(event) => {
                    setCustomDbUrl(event.target.value)
                    if (invalidField === 'custom_db_url') setInvalidField(null)
                  }}
                  placeholder="postgresql://user:pass@ep-...neon.tech/neondb?sslmode=require"
                  autoComplete="off"
                />
                <InputGroupAddon align="inline-end">
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <InputGroupButton
                          type="button"
                          size="icon-xs"
                          aria-label={showCustomUrl ? 'Hide URL' : 'Show URL'}
                          onClick={() => setShowCustomUrl((prev) => !prev)}
                        />
                      }
                    >
                      {showCustomUrl ? (
                        <EyeOff className="size-3.5" aria-hidden="true" />
                      ) : (
                        <Eye className="size-3.5" aria-hidden="true" />
                      )}
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      {showCustomUrl ? 'Hide URL' : 'Show URL'}
                    </TooltipContent>
                  </Tooltip>
                </InputGroupAddon>
              </InputGroup>
              <FieldDescription>
                Passed via a private 0600 file to the subprocess; never persisted in Postgres or
                logs.
              </FieldDescription>
            </Field>
          )}

          {branchMode === 'direct_readonly' && (
            <div className="rounded-lg border p-3">
              <Switch
                label="Confirm read-only JSONL run against configured primary DB"
                checked={allowDirectOptIn}
                onToggle={() => setAllowDirectOptIn((prev) => !prev)}
              />
            </div>
          )}

          {/* Per-role Model Selection */}
          <div className="flex flex-col gap-3 rounded-lg border bg-muted/15 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <KeyRound className="size-3.5 text-muted-foreground" aria-hidden="true" />
                <span
                  className={`${typeClass('body')} text-foreground`}
                  style={{ fontVariationSettings: fontWeights.semibold }}
                >
                  Model selection
                </span>
              </div>
              {onManageProviders && (
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  onClick={() => {
                    onClose()
                    onManageProviders()
                  }}
                  data-testid="manage-providers-link"
                >
                  Manage providers…
                </Button>
              )}
            </div>

            {target === 'rlm' ? (
              <>
                <RoleModelPicker
                  idPrefix={`${formId}-root-lm`}
                  label="Root LM (agent being optimized)"
                  description="Primary reasoning & code-execution model evaluated during GEPA rollouts and holdout."
                  value={rootLm}
                  onChange={setRootLm}
                  providers={localProviders}
                  workspaceDefaultModel={capabilities?.llm_model ?? null}
                  refreshingProviderId={refreshingProviderId}
                  refreshError={providerRefreshError[rootLm.providerId]}
                  onRefreshProvider={(id) => void handleRefreshProvider(id)}
                />

                <RoleModelPicker
                  idPrefix={`${formId}-reflection-lm`}
                  label="Reflection LM (GEPA prompt editor)"
                  description="Model that reads failing traces and proposes mutated RLM instructions each generation."
                  value={reflectionLm}
                  onChange={setReflectionLm}
                  providers={localProviders}
                  workspaceDefaultModel={capabilities?.llm_model ?? null}
                  refreshingProviderId={refreshingProviderId}
                  refreshError={providerRefreshError[reflectionLm.providerId]}
                  onRefreshProvider={(id) => void handleRefreshProvider(id)}
                />
              </>
            ) : (
              <>
                <RoleModelPicker
                  idPrefix={`${formId}-decision-lm`}
                  label="Decision model"
                  description="Calibrated scoring model whose decision thresholds & Choice weights ReAnchor fits."
                  value={decisionLm}
                  onChange={setDecisionLm}
                  providers={localProviders}
                  workspaceDefaultModel={capabilities?.decision_model ?? null}
                  refreshingProviderId={refreshingProviderId}
                  refreshError={providerRefreshError[decisionLm.providerId]}
                  onRefreshProvider={(id) => void handleRefreshProvider(id)}
                />

                {isNonTypeSafeDecisionModel && (
                  <Alert>
                    <AlertDescription className="flex items-start gap-2">
                      <AlertTriangle
                        className="mt-0.5 size-3.5 shrink-0 text-warning"
                        aria-hidden="true"
                      />
                      <span>
                        <strong>Calibration notice:</strong> TypeSafe Jev System One produces
                        calibrated log-probabilities designed for ReAnchor thresholds. Generic LLMs
                        return uncalibrated self-reported scores, which may reduce gate reliability.
                      </span>
                    </AlertDescription>
                  </Alert>
                )}
              </>
            )}
          </div>

          {target === 'rlm' && (
            <>
              <Field>
                <FieldLabel htmlFor={`${formId}-program-kind`}>Program module</FieldLabel>
                <Select
                  value={programKind}
                  onValueChange={(value) => setProgramKind((value || 'rlm') as 'rlm' | 'flex')}
                >
                  <SelectTrigger
                    id={`${formId}-program-kind`}
                    aria-label="RLM program kind"
                    placeholder="Select program"
                  />
                  <SelectContent>
                    <SelectItem index={0} value="rlm">
                      dspy.RLM (Sandboxed Python workspace)
                    </SelectItem>
                    <SelectItem index={1} value="flex">
                      dspy.Flex (Prompt-only · evaluation-only)
                    </SelectItem>
                  </SelectContent>
                </Select>
                <FieldDescription>
                  {programKind === 'flex'
                    ? 'dspy.Flex is evaluation-only for fast prompt experiments and cannot be promoted to live chat serving.'
                    : 'dspy.RLM runs full sandboxed Python workspace rollouts and produces artifacts eligible for live chat promotion.'}
                </FieldDescription>
              </Field>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field data-invalid={invalidField === 'max_metric_calls' || undefined}>
                  <FieldLabel htmlFor={`${formId}-max-calls`}>GEPA max metric calls</FieldLabel>
                  <Input
                    id={`${formId}-max-calls`}
                    type="number"
                    min={1}
                    max={2000}
                    value={maxMetricCalls}
                    onChange={(event) => {
                      setMaxMetricCalls(event.target.value)
                      if (invalidField === 'max_metric_calls') setInvalidField(null)
                    }}
                  />
                  <FieldDescription>Total metric evaluations budgeted for GEPA search.</FieldDescription>
                </Field>

                <Field data-invalid={invalidField === 'holdout_repeats' || undefined}>
                  <FieldLabel htmlFor={`${formId}-holdout-repeats`}>Holdout repeats</FieldLabel>
                  <Input
                    id={`${formId}-holdout-repeats`}
                    type="number"
                    min={1}
                    max={20}
                    value={holdoutRepeats}
                    onChange={(event) => {
                      setHoldoutRepeats(event.target.value)
                      if (invalidField === 'holdout_repeats') setInvalidField(null)
                    }}
                  />
                  <FieldDescription>
                    Evaluates each holdout case N times to catch flaky regressions.
                  </FieldDescription>
                </Field>
              </div>

              <Switch
                label="Disable LM cache on rollouts & holdout"
                checked={disableCache}
                onToggle={() => setDisableCache((prev) => !prev)}
              />

              {capabilities?.allow_local_interpreter === true && (
                <Switch
                  label="Use local DSPy interpreter instead of Daytona"
                  checked={localInterpreter}
                  onToggle={() => setLocalInterpreter((prev) => !prev)}
                />
              )}
            </>
          )}

          <div className="pt-1">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setShowAdvanced((prev) => !prev)}
              data-testid="toggle-advanced-options"
            >
              <ChevronDown
                className={`size-3.5 transition-transform duration-160 ${showAdvanced ? 'rotate-180' : ''}`}
                aria-hidden="true"
              />
              Advanced options
            </Button>
          </div>

          {showAdvanced && (
            <div className="flex flex-col gap-3 rounded-lg border p-3">
              {target === 'rlm' && (
                <>
                  <RoleModelPicker
                    idPrefix={`${formId}-sub-lm`}
                    label="Sub LM (recursive child calls)"
                    description="Faster/cheaper model invoked by nested sub_lm() calls inside the REPL."
                    value={subLm}
                    onChange={setSubLm}
                    providers={localProviders}
                    workspaceDefaultModel={capabilities?.sub_llm_model ?? null}
                    refreshingProviderId={refreshingProviderId}
                    refreshError={providerRefreshError[subLm.providerId]}
                    onRefreshProvider={(id) => void handleRefreshProvider(id)}
                  />

                  <RoleModelPicker
                    idPrefix={`${formId}-judge-lm`}
                    label="Judge LM (semantic grader)"
                    description="Grader model used to score open-ended answers when no exact verifier is specified."
                    value={judgeLm}
                    onChange={setJudgeLm}
                    providers={localProviders}
                    workspaceDefaultModel={capabilities?.llm_model ?? null}
                    refreshingProviderId={refreshingProviderId}
                    refreshError={providerRefreshError[judgeLm.providerId]}
                    onRefreshProvider={(id) => void handleRefreshProvider(id)}
                  />
                </>
              )}

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field data-invalid={invalidField === 'train_ratio' || undefined}>
                  <FieldLabel htmlFor={`${formId}-train-ratio`}>Train ratio override</FieldLabel>
                  <Input
                    id={`${formId}-train-ratio`}
                    type="number"
                    step="0.05"
                    min="0.1"
                    max="0.9"
                    value={trainRatio}
                    onChange={(event) => {
                      setTrainRatio(event.target.value)
                      if (invalidField === 'train_ratio') setInvalidField(null)
                    }}
                    placeholder={target === 'rlm' ? '0.6' : '0.7'}
                  />
                  <FieldDescription>
                    Fraction of dataset lineages reserved for training vs holdout.
                  </FieldDescription>
                </Field>

                <Field data-invalid={invalidField === 'seed' || undefined}>
                  <FieldLabel htmlFor={`${formId}-seed`}>Random seed</FieldLabel>
                  <Input
                    id={`${formId}-seed`}
                    type="number"
                    min={0}
                    value={seed}
                    onChange={(event) => {
                      setSeed(event.target.value)
                      if (invalidField === 'seed') setInvalidField(null)
                    }}
                  />
                  <FieldDescription>Deterministic seed for lineage splits and CV folds.</FieldDescription>
                </Field>
              </div>

              <Switch
                label="Save artifact even if holdout gate does not improve (--force)"
                checked={force}
                onToggle={() => setForce((prev) => !prev)}
              />
            </div>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </form>
      </ScrollArea>

      <DialogFooter className="mx-0 mb-0 border-t px-4 py-3">
        <Button type="button" variant="outline" disabled={submitting} onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" form={formId} disabled={submitting} data-testid="submit-launch-run">
          <Play className="size-3.5" aria-hidden="true" />
          {submitting ? 'Starting…' : 'Start optimization'}
        </Button>
      </DialogFooter>
    </>
  )
}

export function LaunchOptimizationDialog({
  open,
  onOpenChange,
  api,
  capabilities,
  datasets,
  providers = [],
  defaults,
  onStarted,
  onManageProviders,
  onProviderRefreshed,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  api: FleetApi
  capabilities: OptimizationCapabilities | null
  datasets: OptimizationDatasetSummary[]
  providers?: LLMProviderView[]
  defaults: LaunchDefaults
  onStarted: (job: OptimizationJobDetail) => void
  onManageProviders?: () => void
  onProviderRefreshed?: (provider: LLMProviderView) => void
}) {
  const [openSession, setOpenSession] = useState(0)
  const [wasOpen, setWasOpen] = useState(open)

  if (wasOpen !== open) {
    setWasOpen(open)
    if (open) {
      setOpenSession((prev) => prev + 1)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[min(780px,calc(100dvh-4rem))] flex-col gap-0 p-0 sm:max-w-xl">
        {open && (
          <LaunchOptimizationFormBody
            key={openSession}
            api={api}
            capabilities={capabilities}
            datasets={datasets}
            providers={providers}
            defaults={defaults}
            onClose={() => onOpenChange(false)}
            onStarted={onStarted}
            onManageProviders={onManageProviders}
            onProviderRefreshed={onProviderRefreshed}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
