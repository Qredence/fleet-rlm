import { ChevronDown, Eye, EyeOff, Play } from 'lucide-react'
import { useEffect, useId, useMemo, useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldDescription, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/components/ui/input-group'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  errorMessage,
  type FleetApi,
  type OptimizationBranchMode,
  type OptimizationCapabilities,
  type OptimizationDatasetSummary,
  type OptimizationJobCreate,
  type OptimizationJobDetail,
  type OptimizationTarget,
} from '@/lib/api'

export interface LaunchDefaults {
  target?: OptimizationTarget
  datasetId?: string
  programKind?: 'rlm' | 'flex'
  seed?: number
  maxMetricCalls?: number
  holdoutRepeats?: number
}

export function LaunchOptimizationDialog({
  open,
  onOpenChange,
  api,
  capabilities,
  datasets,
  defaults,
  onStarted,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  api: FleetApi
  capabilities: OptimizationCapabilities | null
  datasets: OptimizationDatasetSummary[]
  defaults: LaunchDefaults
  onStarted: (job: OptimizationJobDetail) => void
}) {
  const formId = useId()
  const [target, setTarget] = useState<OptimizationTarget>(defaults.target ?? 'skills')
  const [datasetId, setDatasetId] = useState<string>(defaults.datasetId ?? '')
  const [programKind, setProgramKind] = useState<'rlm' | 'flex'>(defaults.programKind ?? 'rlm')
  const [branchMode, setBranchMode] = useState<OptimizationBranchMode>('configured_branch')
  const [customDbUrl, setCustomDbUrl] = useState('')
  const [showCustomUrl, setShowCustomUrl] = useState(false)
  const [allowDirectOptIn, setAllowDirectOptIn] = useState(false)

  const [maxMetricCalls, setMaxMetricCalls] = useState('80')
  const [holdoutRepeats, setHoldoutRepeats] = useState('3')
  const [trainRatio, setTrainRatio] = useState('')
  const [seed, setSeed] = useState('0')
  const [disableCache, setDisableCache] = useState(true)
  const [localInterpreter, setLocalInterpreter] = useState(false)
  const [force, setForce] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const matchingDatasets = useMemo(
    () => datasets.filter((item) => item.target === target),
    [datasets, target],
  )

  // Reset form coherently each time the dialog opens.
  useEffect(() => {
    if (!open) return
    const nextTarget = defaults.target ?? 'skills'
    const forTarget = datasets.filter((item) => item.target === nextTarget)
    const fallbackDataset =
      defaults.datasetId && forTarget.some((item) => item.id === defaults.datasetId)
        ? defaults.datasetId
        : (forTarget[0]?.id ?? (nextTarget === 'rlm' ? 'db' : ''))
    const defaultBranch: OptimizationBranchMode = capabilities?.auto_neon_enabled
      ? 'auto_neon'
      : capabilities?.configured_branch_enabled
        ? 'configured_branch'
        : 'custom_branch'

    setTarget(nextTarget)
    setDatasetId(fallbackDataset)
    setProgramKind(defaults.programKind ?? 'rlm')
    setBranchMode(defaultBranch)
    setCustomDbUrl('')
    setShowCustomUrl(false)
    setAllowDirectOptIn(false)
    setMaxMetricCalls(String(defaults.maxMetricCalls ?? 80))
    setHoldoutRepeats(String(defaults.holdoutRepeats ?? 3))
    setTrainRatio('')
    setSeed(String(defaults.seed ?? 0))
    setDisableCache(true)
    setLocalInterpreter(false)
    setForce(false)
    setShowAdvanced(false)
    setError('')
  }, [capabilities, datasets, defaults, open])

  const handleTargetChange = (nextTarget: OptimizationTarget) => {
    setTarget(nextTarget)
    const forTarget = datasets.filter((item) => item.target === nextTarget)
    setDatasetId(forTarget[0]?.id ?? (nextTarget === 'rlm' ? 'db' : ''))
    setError('')
  }

  async function submitForm() {
    setError('')
    const parsedSeed = Number.parseInt(seed, 10)
    if (Number.isNaN(parsedSeed) || parsedSeed < 0) {
      setError('Seed must be a non-negative integer.')
      return
    }
    if (branchMode === 'custom_branch' && !customDbUrl.trim()) {
      setError('Enter an isolated Neon branch PostgreSQL URL.')
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
        return
      }
      payload.train_ratio = ratio
    }
    if (target === 'rlm') {
      const budget = Number.parseInt(maxMetricCalls, 10)
      const repeats = Number.parseInt(holdoutRepeats, 10)
      if (Number.isNaN(budget) || budget < 1) {
        setError('Max metric calls must be a positive integer.')
        return
      }
      if (Number.isNaN(repeats) || repeats < 1) {
        setError('Holdout repeats must be a positive integer.')
        return
      }
      payload.program_kind = programKind
      payload.max_metric_calls = budget
      payload.holdout_repeats = repeats
      payload.disable_cache = disableCache
      payload.local_interpreter = localInterpreter
    }

    setSubmitting(true)
    try {
      const started = await api.createOptimizationJob(payload)
      onOpenChange(false)
      onStarted(started)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !submitting && onOpenChange(nextOpen)}>
      <DialogContent className="flex max-h-[min(720px,calc(100dvh-4rem))] flex-col gap-0 p-0 sm:max-w-lg">
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
            className="flex flex-col gap-3 p-0.5"
          >
            <Field>
              <FieldLabel>Target</FieldLabel>
              <Select
                value={target}
                onValueChange={(value) => handleTargetChange((value || 'skills') as OptimizationTarget)}
              >
                <SelectTrigger aria-label="Optimization target" placeholder="Select target" />
                <SelectContent>
                  <SelectItem index={0} value="skills">Skill Selector (ReAnchor)</SelectItem>
                  <SelectItem index={1} value="memory">Memory Gate (ReAnchor)</SelectItem>
                  <SelectItem index={2} value="rlm">Root RLM Program (GEPA)</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            <Field>
              <FieldLabel>Dataset</FieldLabel>
              <Select value={datasetId} onValueChange={setDatasetId}>
                <SelectTrigger aria-label="Evaluation dataset" placeholder="Choose dataset" />
                <SelectContent>
                  {matchingDatasets.map((item, idx) => (
                    <SelectItem key={item.id} index={idx} value={item.id}>
                      {`${item.name} (${item.row_count} rows · ${item.source})`}
                    </SelectItem>
                  ))}
                  {target === 'rlm' && (
                    <SelectItem index={matchingDatasets.length} value="db">
                      Past Completed Neon Runs (DB fallback)
                    </SelectItem>
                  )}
                </SelectContent>
              </Select>
            </Field>

            <Field>
              <FieldLabel>Neon database isolation</FieldLabel>
              <Select
                value={branchMode}
                onValueChange={(value) => {
                  setBranchMode((value || 'configured_branch') as OptimizationBranchMode)
                  setError('')
                }}
              >
                <SelectTrigger aria-label="Neon database isolation" placeholder="Select branch mode" />
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
                  <SelectItem index={2} value="custom_branch">Custom Neon branch URL</SelectItem>
                  <SelectItem index={3} value="direct_readonly" disabled={datasetId === 'db'}>
                    Primary DB (read-only JSONL only)
                  </SelectItem>
                </SelectContent>
              </Select>
              <FieldDescription>
                Offline runs never silently write to your primary serving database.
              </FieldDescription>
            </Field>

            {branchMode === 'custom_branch' && (
              <Field data-invalid={Boolean(error) || undefined}>
                <FieldLabel htmlFor="opt-custom-branch-url">Isolated Neon branch URL</FieldLabel>
                <InputGroup>
                  <InputGroupInput
                    id="opt-custom-branch-url"
                    type={showCustomUrl ? 'text' : 'password'}
                    value={customDbUrl}
                    onChange={(event) => setCustomDbUrl(event.target.value)}
                    placeholder="postgresql://user:pass@ep-...neon.tech/neondb?sslmode=require"
                    autoComplete="off"
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupButton
                      type="button"
                      size="icon-xs"
                      aria-label={showCustomUrl ? 'Hide URL' : 'Show URL'}
                      onClick={() => setShowCustomUrl((prev) => !prev)}
                    >
                      {showCustomUrl ? (
                        <EyeOff className="size-3.5" aria-hidden="true" />
                      ) : (
                        <Eye className="size-3.5" aria-hidden="true" />
                      )}
                    </InputGroupButton>
                  </InputGroupAddon>
                </InputGroup>
                <FieldDescription>
                  Passed only to the subprocess; never persisted in Postgres or logs.
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

            {target === 'rlm' && (
              <>
                <Field>
                  <FieldLabel>Program module</FieldLabel>
                  <Select
                    value={programKind}
                    onValueChange={(value) => setProgramKind((value || 'rlm') as 'rlm' | 'flex')}
                  >
                    <SelectTrigger aria-label="RLM program kind" placeholder="Select program" />
                    <SelectContent>
                      <SelectItem index={0} value="rlm">dspy.RLM (Sandboxed Python workspace)</SelectItem>
                      <SelectItem index={1} value="flex">dspy.Flex (Prompt-only)</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>

                <Field>
                  <FieldLabel htmlFor="opt-max-calls">GEPA max metric calls</FieldLabel>
                  <Input
                    id="opt-max-calls"
                    type="number"
                    min={1}
                    max={2000}
                    value={maxMetricCalls}
                    onChange={(event) => setMaxMetricCalls(event.target.value)}
                  />
                </Field>

                <Field>
                  <FieldLabel htmlFor="opt-holdout-repeats">Holdout repeats</FieldLabel>
                  <Input
                    id="opt-holdout-repeats"
                    type="number"
                    min={1}
                    max={20}
                    value={holdoutRepeats}
                    onChange={(event) => setHoldoutRepeats(event.target.value)}
                  />
                </Field>

                <Switch
                  label="Disable LM cache on rollouts & holdout"
                  checked={disableCache}
                  onToggle={() => setDisableCache((prev) => !prev)}
                />
                <Switch
                  label="Use local DSPy interpreter instead of Daytona"
                  checked={localInterpreter}
                  onToggle={() => setLocalInterpreter((prev) => !prev)}
                />
              </>
            )}

            <div className="pt-1">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setShowAdvanced((prev) => !prev)}
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
                <Field>
                  <FieldLabel htmlFor="opt-train-ratio">Train ratio override</FieldLabel>
                  <Input
                    id="opt-train-ratio"
                    type="number"
                    step="0.05"
                    min="0.1"
                    max="0.9"
                    value={trainRatio}
                    onChange={(event) => setTrainRatio(event.target.value)}
                    placeholder={target === 'rlm' ? '0.6' : '0.7'}
                  />
                </Field>

                <Field>
                  <FieldLabel htmlFor="opt-seed">Random seed</FieldLabel>
                  <Input
                    id="opt-seed"
                    type="number"
                    min={0}
                    value={seed}
                    onChange={(event) => setSeed(event.target.value)}
                  />
                </Field>

                <Switch
                  label="Save artifact even if holdout gate does not improve (--force)"
                  checked={force}
                  onToggle={() => setForce((prev) => !prev)}
                />
              </div>
            )}

            {error && (
              <Alert variant="destructive">
                <AlertDescription>
                  <FieldError>{error}</FieldError>
                </AlertDescription>
              </Alert>
            )}
          </form>
        </ScrollArea>

        <DialogFooter className="border-t px-4 py-3">
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="submit" form={formId} disabled={submitting} data-testid="submit-launch-run">
            <Play className="size-3.5" aria-hidden="true" />
            {submitting ? 'Starting…' : 'Start optimization'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
