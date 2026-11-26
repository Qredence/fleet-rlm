import { formatScore as formatHoldoutScore, holdoutPoints } from './holdout-score'
import {
  ArrowUpRight,
  CheckCircle2,
  Clock,
  FileDiff,
  ListFilter,
  ScrollText,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockLine,
  CodeBlockTitle,
} from '@/components/ui/code-block'
import { Tabs, TabsItem, TabsPanel } from '@/components/ui/fluid-tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { OptimizationJobDetail, OptimizationStageTimelineEntry } from '@/lib/api'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

export interface GateCheckRow {
  name: string
  label: string
  passed: boolean
  actual: unknown
  threshold: unknown
  reason: string
}

interface ParameterDiffRow {
  gate: string
  parameter: string
  baseline: number
  fitted: number
  delta: number
}

interface CandidateHistoryRow {
  candidate_idx: number
  val_score: number | null
  parent: number | null
  parents: number[]
  accepted: boolean
  is_best: boolean
  metric_calls: number | null
  reflection_summary: string | null
}

interface CaseDeltaRow {
  case_id: string
  lineage: string | null
  input_preview: string
  expected?: unknown
  baseline_pred?: unknown
  baseline_prob?: number | null
  candidate_pred?: unknown
  candidate_prob?: number | null
  baseline_score: number | null
  candidate_score: number | null
  baseline_correctness?: number | null
  candidate_correctness?: number | null
  baseline_pass_rate?: number | null
  candidate_pass_rate?: number | null
  delta: number | null
  repeats?: number | null
  blocking: boolean
  blocking_failed: boolean
  paired_regression: boolean
  outcome: 'fixed' | 'regressed' | 'unchanged'
  baseline_feedback?: string
  candidate_feedback?: string
}

interface DiffLineEntry {
  variant: 'added' | 'removed' | 'highlighted' | 'default'
  text: string
}

const SHEET_TABS = {
  gates: 0,
  diff: 1,
  cases: 2,
  logs: 3,
} as const

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatScore(value: number | null | undefined, isRlmHundredScale = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const pct = isRlmHundredScale && value > 1.0001 ? value : value * 100
  return `${pct.toFixed(1)}%`
}

function formatNumber(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function formatActualValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return String(value)
    return value.toFixed(4)
  }
  return String(value)
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return '—'
  }
  if (seconds < 1) return `${Math.max(1, Math.round(seconds * 1000))}ms`
  if (seconds < 10) return `${seconds.toFixed(1)}s`
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const rem = Math.round(seconds % 60)
  return rem > 0 ? `${mins}m ${rem}s` : `${mins}m`
}

function parseGateChecks(job: OptimizationJobDetail): GateCheckRow[] {
  const report = asRecord(job.report_json)
  const raw = report?.gate_checks
  if (Array.isArray(raw) && raw.length > 0) {
    return raw.flatMap((item): GateCheckRow[] => {
      const rec = asRecord(item)
      if (!rec) return []
      return [
        {
          name: typeof rec.name === 'string' ? rec.name : 'gate',
          label: typeof rec.label === 'string' ? rec.label : String(rec.name ?? 'Gate check'),
          passed: rec.passed === true,
          actual: rec.actual,
          threshold: rec.threshold,
          reason: typeof rec.reason === 'string' ? rec.reason : '',
        },
      ]
    })
  }

  // Fallback for legacy runs created before explicit gate_checks telemetry.
  if (job.status !== 'completed') return []
  const fallback: GateCheckRow[] = []
  if (job.baseline_score !== null && job.optimized_score !== null) {
    const improved = job.optimized_score > job.baseline_score
    fallback.push({
      name: 'holdout_score_improved',
      label: 'Holdout score improved over baseline',
      passed: improved,
      actual: job.optimized_score,
      threshold: `> ${job.baseline_score.toFixed(4)}`,
      reason: improved
        ? `Holdout improved from ${formatHoldoutScore(job.baseline_score, job.target)} to ${formatHoldoutScore(job.optimized_score, job.target)}`
        : `Holdout ${formatHoldoutScore(job.optimized_score, job.target)} did not beat baseline ${formatHoldoutScore(job.baseline_score, job.target)}`,
    })
  }
  if (job.target !== 'rlm') {
    const fpBefore = asNumber(report?.fp_rate_before)
    const fpAfter = asNumber(report?.fp_rate_after)
    if (fpBefore !== null && fpAfter !== null) {
      const ok = fpAfter <= fpBefore
      fallback.push({
        name: 'fp_rate_non_regression',
        label: 'False-positive rate did not regress',
        passed: ok,
        actual: fpAfter,
        threshold: `<= ${fpBefore.toFixed(4)}`,
        reason: ok
          ? `FP rate ${formatScore(fpAfter)} <= baseline ${formatScore(fpBefore)}`
          : `FP rate regressed from ${formatScore(fpBefore)} to ${formatScore(fpAfter)}`,
      })
    }
  } else {
    const blockingCount = Array.isArray(report?.blocking_failures)
      ? report.blocking_failures.length
      : null
    const regCount = Array.isArray(report?.paired_regressions)
      ? report.paired_regressions.length
      : null
    if (blockingCount !== null) {
      fallback.push({
        name: 'zero_blocking_failures',
        label: 'Zero blocking case failures',
        passed: blockingCount === 0,
        actual: blockingCount,
        threshold: 0,
        reason:
          blockingCount === 0
            ? 'All blocking holdout cases passed'
            : `${blockingCount} blocking holdout failure(s)`,
      })
    }
    if (regCount !== null) {
      fallback.push({
        name: 'zero_paired_regressions',
        label: 'Zero paired case regressions',
        passed: regCount === 0,
        actual: regCount,
        threshold: 0,
        reason:
          regCount === 0
            ? 'No holdout case pass rate regressed'
            : `${regCount} paired regression(s) detected`,
      })
    }
  }
  return fallback
}

function parseParameterDiffs(report: Record<string, unknown> | null): ParameterDiffRow[] {
  const raw = report?.parameter_diffs
  if (Array.isArray(raw) && raw.length > 0) {
    return raw.flatMap((item): ParameterDiffRow[] => {
      const rec = asRecord(item)
      if (!rec) return []
      const baseline = asNumber(rec.baseline)
      const fitted = asNumber(rec.fitted)
      if (baseline === null || fitted === null) return []
      return [
        {
          gate: typeof rec.gate === 'string' ? rec.gate : 'gate',
          parameter: typeof rec.parameter === 'string' ? rec.parameter : 'threshold',
          baseline,
          fitted,
          delta: asNumber(rec.delta) ?? fitted - baseline,
        },
      ]
    })
  }

  // Fallback: derive from fitted_parameters against ReAnchor defaults (0.50 threshold, 1.0 weight).
  const fitted = asRecord(report?.fitted_parameters)
  if (!fitted) return []
  const rows: ParameterDiffRow[] = []
  for (const [gate, rawCfg] of Object.entries(fitted)) {
    const cfg = asRecord(rawCfg)
    if (!cfg) continue
    const threshold = asNumber(cfg.threshold)
    if (threshold !== null) {
      rows.push({
        gate,
        parameter: 'threshold',
        baseline: 0.5,
        fitted: threshold,
        delta: threshold - 0.5,
      })
    }
    const weights = asRecord(cfg.weights)
    if (weights) {
      for (const [opt, rawWeight] of Object.entries(weights)) {
        const w = asNumber(rawWeight)
        if (w !== null) {
          rows.push({
            gate,
            parameter: `weight:${opt}`,
            baseline: 1.0,
            fitted: w,
            delta: w - 1.0,
          })
        }
      }
    }
  }
  return rows
}

function parseCandidateHistory(report: Record<string, unknown> | null): CandidateHistoryRow[] {
  const raw = report?.candidate_history
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item, idx): CandidateHistoryRow[] => {
    const rec = asRecord(item)
    if (!rec) return []
    const parents = Array.isArray(rec.parents)
      ? rec.parents.filter((p): p is number => typeof p === 'number')
      : []
    return [
      {
        candidate_idx: typeof rec.candidate_idx === 'number' ? rec.candidate_idx : idx,
        val_score: asNumber(rec.val_score),
        parent: typeof rec.parent === 'number' ? rec.parent : (parents[0] ?? null),
        parents,
        accepted: rec.accepted === true,
        is_best: rec.is_best === true,
        metric_calls: typeof rec.metric_calls === 'number' ? rec.metric_calls : null,
        reflection_summary:
          typeof rec.reflection_summary === 'string' ? rec.reflection_summary : null,
      },
    ]
  })
}

function parseCaseDeltas(report: Record<string, unknown> | null): CaseDeltaRow[] {
  const raw = report?.case_deltas
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item, idx): CaseDeltaRow[] => {
    const rec = asRecord(item)
    if (!rec) return []
    const outcomeRaw = rec.outcome
    const outcome: 'fixed' | 'regressed' | 'unchanged' =
      outcomeRaw === 'fixed' || outcomeRaw === 'regressed' ? outcomeRaw : 'unchanged'
    return [
      {
        case_id: typeof rec.case_id === 'string' ? rec.case_id : `case-${idx + 1}`,
        lineage: typeof rec.lineage === 'string' ? rec.lineage : null,
        input_preview: typeof rec.input_preview === 'string' ? rec.input_preview : '',
        expected: rec.expected,
        baseline_pred: rec.baseline_pred,
        baseline_prob: asNumber(rec.baseline_prob),
        candidate_pred: rec.candidate_pred,
        candidate_prob: asNumber(rec.candidate_prob),
        baseline_score: asNumber(rec.baseline_score),
        candidate_score: asNumber(rec.candidate_score),
        baseline_correctness: asNumber(rec.baseline_correctness),
        candidate_correctness: asNumber(rec.candidate_correctness),
        baseline_pass_rate: asNumber(rec.baseline_pass_rate),
        candidate_pass_rate: asNumber(rec.candidate_pass_rate),
        delta: asNumber(rec.delta),
        repeats: typeof rec.repeats === 'number' ? rec.repeats : null,
        blocking: rec.blocking === true,
        blocking_failed: rec.blocking_failed === true,
        paired_regression: rec.paired_regression === true,
        outcome,
        baseline_feedback: typeof rec.baseline_feedback === 'string' ? rec.baseline_feedback : '',
        candidate_feedback:
          typeof rec.candidate_feedback === 'string' ? rec.candidate_feedback : '',
      },
    ]
  })
}

function buildFallbackLineDiff(before: string, after: string): DiffLineEntry[] {
  const a = before ? before.split('\n') : []
  const b = after ? after.split('\n') : []
  if (before === after) return []

  // Simple LCS line diff (bounded to keep UI instant for long prompts).
  const n = a.length
  const m = b.length
  if (n * m > 250_000) {
    return [
      ...a.map((text): DiffLineEntry => ({ variant: 'removed', text })),
      ...b.map((text): DiffLineEntry => ({ variant: 'added', text })),
    ]
  }
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const result: DiffLineEntry[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      result.push({ variant: 'default', text: a[i] })
      i += 1
      j += 1
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      result.push({ variant: 'removed', text: a[i] })
      i += 1
    } else {
      result.push({ variant: 'added', text: b[j] })
      j += 1
    }
  }
  while (i < n) {
    result.push({ variant: 'removed', text: a[i] })
    i += 1
  }
  while (j < m) {
    result.push({ variant: 'added', text: b[j] })
    j += 1
  }
  return result
}

function parseUnifiedDiff(diffText: string): DiffLineEntry[] {
  if (!diffText.trim()) return []
  return diffText.split('\n').map((line): DiffLineEntry => {
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) {
      return { variant: 'highlighted', text: line }
    }
    if (line.startsWith('+')) {
      return { variant: 'added', text: line.slice(1) }
    }
    if (line.startsWith('-')) {
      return { variant: 'removed', text: line.slice(1) }
    }
    return { variant: 'default', text: line.startsWith(' ') ? line.slice(1) : line }
  })
}

function parseTimeline(job: OptimizationJobDetail): OptimizationStageTimelineEntry[] {
  const raw = job.progress_json?.stage_timeline
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is OptimizationStageTimelineEntry =>
    Boolean(item && typeof item === 'object' && typeof item.stage === 'string'),
  )
}

interface CalibrationRow {
  label: string
  before: number | null
  after: number | null
  lowerBetter: boolean
  isCount?: boolean
}

function buildCalibrationMatrix(
  job: OptimizationJobDetail,
  report: Record<string, unknown> | null,
): CalibrationRow[] {
  if (!report) return []
  if (job.target === 'rlm') {
    const metrics = asRecord(report.metrics)
    if (!metrics) return []
    return [
      {
        label: 'Holdout score',
        before: asNumber(metrics.baseline_holdout_score) ?? job.baseline_score,
        after: asNumber(metrics.optimized_holdout_score) ?? job.optimized_score,
        lowerBetter: false,
      },
      {
        label: 'Correctness',
        before: asNumber(metrics.baseline_correctness),
        after: asNumber(metrics.optimized_correctness),
        lowerBetter: false,
      },
      {
        label: 'Submit rate',
        before: asNumber(metrics.baseline_submit_rate),
        after: asNumber(metrics.optimized_submit_rate),
        lowerBetter: false,
      },
      {
        label: 'Mean REPL actions',
        before: asNumber(metrics.baseline_mean_actions),
        after: asNumber(metrics.optimized_mean_actions),
        lowerBetter: true,
        isCount: true,
      },
      {
        label: 'Python errors',
        before: asNumber(metrics.baseline_py_errors),
        after: asNumber(metrics.optimized_py_errors),
        lowerBetter: true,
        isCount: true,
      },
    ].filter((row) => row.before !== null || row.after !== null)
  }

  const before = asRecord(report.diagnostics_before) ?? {}
  const after = asRecord(report.diagnostics_after) ?? {}
  const candidates: Array<{
    key: string
    label: string
    lowerBetter: boolean
    isCount?: boolean
    fallbackBefore?: unknown
    fallbackAfter?: unknown
  }> = [
    {
      key: 'fp_rate',
      label: 'False-positive rate',
      lowerBetter: true,
      fallbackBefore: report.fp_rate_before,
      fallbackAfter: report.fp_rate_after,
    },
    { key: 'fn_rate', label: 'False-negative rate', lowerBetter: true },
    { key: 'primary_accuracy', label: 'Primary skill accuracy', lowerBetter: false },
    { key: 'secondary_hit_rate', label: 'Secondary skill hit rate', lowerBetter: false },
    { key: 'action_accuracy', label: 'Memory action accuracy', lowerBetter: false },
    { key: 'scope_accuracy', label: 'Memory scope accuracy', lowerBetter: false },
    { key: 'multi_turn_accuracy', label: 'Multi-turn accuracy', lowerBetter: false },
    {
      key: 'auto_approve_fp_count',
      label: 'Auto-approve false positives',
      lowerBetter: true,
      isCount: true,
      fallbackBefore: report.auto_approve_fp_count_before,
      fallbackAfter: report.auto_approve_fp_count_after,
    },
  ]

  return candidates
    .map((item): CalibrationRow => ({
      label: item.label,
      before: asNumber(before[item.key] ?? item.fallbackBefore),
      after: asNumber(after[item.key] ?? item.fallbackAfter),
      lowerBetter: item.lowerBetter,
      isCount: item.isCount,
    }))
    .filter((row) => row.before !== null || row.after !== null)
}

function formatMatrixValue(value: number | null, isCount?: boolean): string {
  if (value === null) return '—'
  if (isCount) return Number.isInteger(value) ? String(value) : value.toFixed(2)
  const pct = value > 1.0001 ? value : value * 100
  return `${pct.toFixed(1)}%`
}

function formatMatrixDelta(
  before: number | null,
  after: number | null,
  lowerBetter: boolean,
  isCount?: boolean,
): { text: string; tone: 'good' | 'bad' | 'neutral' } {
  if (before === null || after === null) return { text: '—', tone: 'neutral' }
  const diff = after - before
  if (Math.abs(diff) < 1e-6) return { text: '0.0', tone: 'neutral' }
  const improved = lowerBetter ? diff < 0 : diff > 0
  const sign = diff > 0 ? '+' : ''
  const text = isCount
    ? `${sign}${Number.isInteger(diff) ? diff : diff.toFixed(2)}`
    : `${sign}${((Math.abs(before) > 1.0001 || Math.abs(after) > 1.0001 ? diff : diff * 100)).toFixed(1)} pts`
  return { text, tone: improved ? 'good' : 'bad' }
}

interface ModelProvenanceRow {
  role: string
  model: string
  dialect: string | null
  providerName: string | null
}

function extractModelsProvenance(job: OptimizationJobDetail): ModelProvenanceRow[] {
  const report = asRecord(job.report_json)
  const modelsRec = asRecord(report?.models)
  const roleLabels: Array<[string, string]> = [
    ['root_lm', 'Root LM'],
    ['sub_lm', 'Sub LM'],
    ['reflection_lm', 'Reflection LM'],
    ['judge_lm', 'Judge LM'],
    ['decision_lm', 'Decision LM'],
  ]
  const rows: ModelProvenanceRow[] = []
  if (modelsRec) {
    for (const [key, label] of roleLabels) {
      const entry = asRecord(modelsRec[key])
      if (!entry) continue
      const model = typeof entry.model === 'string' ? entry.model : ''
      if (!model) continue
      rows.push({
        role: label,
        model,
        dialect: typeof entry.dialect === 'string' ? entry.dialect : null,
        providerName: typeof entry.provider_name === 'string' ? entry.provider_name : null,
      })
    }
  }
  if (rows.length === 0 && job.resolved_decision_model) {
    rows.push({
      role: 'Decision LM',
      model: job.resolved_decision_model,
      dialect: null,
      providerName: null,
    })
  }
  return rows
}

function PredictorDiffCard({
  name,
  diffText,
  beforeText,
  afterText,
}: {
  name: string
  diffText: string
  beforeText: string
  afterText: string
}) {
  const [mode, setMode] = useState<'diff' | 'full'>('diff')

  const diffLines = useMemo(() => {
    if (diffText.trim()) return parseUnifiedDiff(diffText)
    return buildFallbackLineDiff(beforeText, afterText)
  }, [afterText, beforeText, diffText])

  const addedCount = diffLines.filter((line) => line.variant === 'added').length
  const removedCount = diffLines.filter((line) => line.variant === 'removed').length
  const changed = addedCount > 0 || removedCount > 0 || beforeText !== afterText

  return (
    <CodeBlock size="sm" data-testid={`instruction-diff-${name}`}>
      <CodeBlockHeader className="flex-wrap">
        <CodeBlockTitle className="flex items-center gap-2">
          <span className="font-mono">{name}</span>
          {changed ? (
            <Badge variant="secondary" className="tabular-nums">
              +{addedCount} / −{removedCount}
            </Badge>
          ) : (
            <Badge variant="outline">Unchanged</Badge>
          )}
        </CodeBlockTitle>
        <CodeBlockActions>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              size="xs"
              variant={mode === 'diff' ? 'secondary' : 'ghost'}
              onClick={() => setMode('diff')}
            >
              Diff
            </Button>
            <Button
              type="button"
              size="xs"
              variant={mode === 'full' ? 'secondary' : 'ghost'}
              onClick={() => setMode('full')}
            >
              Full text
            </Button>
            <Tooltip>
              <TooltipTrigger render={<span className="inline-flex" />}>
                <CodeBlockCopyButton
                  value={mode === 'diff' && diffText ? diffText : afterText}
                  aria-label={`Copy ${name} ${mode === 'diff' ? 'diff' : 'instructions'}`}
                />
              </TooltipTrigger>
              <TooltipContent side="top">
                Copy {mode === 'diff' ? 'unified diff' : 'optimized instructions'}
              </TooltipContent>
            </Tooltip>
          </div>
        </CodeBlockActions>
      </CodeBlockHeader>
      <CodeBlockContent
        wrap
        className="max-h-80 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
      >
        {mode === 'full' ? (
          afterText || 'No optimized instructions recorded.'
        ) : diffLines.length === 0 ? (
          <span className="text-muted-foreground">Instructions unchanged from baseline.</span>
        ) : (
          diffLines.map((line, idx) => (
            <CodeBlockLine
              key={idx}
              variant={line.variant}
              className={
                line.variant === 'added'
                  ? 'bg-success/10 pl-4 after:text-success'
                  : line.variant === 'removed'
                    ? 'pl-4'
                    : line.variant === 'highlighted'
                      ? 'text-muted-foreground'
                      : 'pl-4'
              }
            >
              {line.text}
            </CodeBlockLine>
          ))
        )}
      </CodeBlockContent>
    </CodeBlock>
  )
}

export function RunFullDiffContent({
  job,
  initialTab = SHEET_TABS.gates,
}: {
  job: OptimizationJobDetail
  initialTab?: number
}) {
  const [activeTab, setActiveTab] = useState<number>(initialTab)
  const [caseFilter, setCaseFilter] = useState<'all' | 'regressed' | 'fixed' | 'blocking'>('all')

  const report = asRecord(job.report_json)
  const gateChecks = useMemo(() => parseGateChecks(job), [job])
  const parameterDiffs = useMemo(() => parseParameterDiffs(report), [report])
  const candidateHistory = useMemo(() => parseCandidateHistory(report), [report])
  const caseDeltas = useMemo(() => parseCaseDeltas(report), [report])
  const timeline = useMemo(() => parseTimeline(job), [job])
  const calibrationRows = useMemo(() => buildCalibrationMatrix(job, report), [job, report])
  const modelsProvenance = useMemo(() => extractModelsProvenance(job), [job])

  const instructionDiffs = asRecord(report?.instruction_diffs)
  const baseInstructions = asRecord(report?.baseline_instructions)
  const optInstructions = asRecord(report?.optimized_instructions)
  const predictorNames = useMemo(() => {
    const set = new Set<string>()
    for (const key of Object.keys(instructionDiffs ?? {})) set.add(key)
    for (const key of Object.keys(optInstructions ?? {})) set.add(key)
    for (const key of Object.keys(baseInstructions ?? {})) set.add(key)
    return Array.from(set)
  }, [baseInstructions, instructionDiffs, optInstructions])

  const regressedCount = caseDeltas.filter(
    (c) => c.outcome === 'regressed' || c.paired_regression || c.blocking_failed,
  ).length
  const fixedCount = caseDeltas.filter((c) => c.outcome === 'fixed').length
  const blockingCount = caseDeltas.filter((c) => c.blocking || c.blocking_failed).length

  const filteredCases = useMemo(() => {
    if (caseFilter === 'regressed') {
      return caseDeltas.filter(
        (c) => c.outcome === 'regressed' || c.paired_regression || c.blocking_failed,
      )
    }
    if (caseFilter === 'fixed') {
      return caseDeltas.filter((c) => c.outcome === 'fixed')
    }
    if (caseFilter === 'blocking') {
      return caseDeltas.filter((c) => c.blocking || c.blocking_failed)
    }
    return caseDeltas
  }, [caseDeltas, caseFilter])

  const allGatesPassed =
    gateChecks.length > 0
      ? gateChecks.every((check) => check.passed)
      : job.saved === true && report?.eligible !== false

  const safeMlflowUrl =
    job.mlflow_run_url && /^https?:\/\//i.test(job.mlflow_run_url) ? job.mlflow_run_url : null

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="run-full-diff-content">
      <div className="border-b px-4 py-2.5">
        <Tabs
          selectedIndex={activeTab}
          onSelect={setActiveTab}
          idPrefix="opt-diff-tabs"
          size="compact"
          className="workspace-fluid-tabs"
        >
          <TabsItem index={SHEET_TABS.gates} icon={ShieldCheck} label="Gates & Timeline" />
          <TabsItem index={SHEET_TABS.diff} icon={FileDiff} label="What Changed" />
          <TabsItem
            index={SHEET_TABS.cases}
            icon={ListFilter}
            label={`Holdout Cases (${caseDeltas.length})`}
          />
          <TabsItem index={SHEET_TABS.logs} icon={ScrollText} label="Logs & Provenance" />
        </Tabs>
      </div>

      <ScrollArea
        className="flex min-h-0 flex-1 flex-col"
        viewportClassName="min-h-0 flex-1 !h-auto p-4"
      >
        <div className="flex flex-col gap-5 pb-4">
          {/* Tab 0: Gate Checks & Stage Timeline */}
          <TabsPanel index={SHEET_TABS.gates} selectedIndex={activeTab} idPrefix="opt-diff-tabs">
            <div className="flex flex-col gap-5">
              {/* Verdict Summary Banner */}
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-3.5">
                <div className="flex items-start gap-2.5">
                  {allGatesPassed && job.status === 'completed' ? (
                    <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-hidden="true" />
                  ) : (
                    <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
                  )}
                  <div>
                    <p
                      className={`${typeClass('subtitle')} text-foreground`}
                      style={{ fontVariationSettings: fontWeights.semibold }}
                    >
                      {job.status !== 'completed'
                        ? `Run ${job.status}`
                        : allGatesPassed
                          ? 'Passed all promotion quality gates'
                          : 'Gate rejected candidate'}
                    </p>
                    <p className={`${typeClass('caption')} text-muted-foreground`}>
                      {job.baseline_score !== null && job.optimized_score !== null
                        ? `Holdout moved from ${formatHoldoutScore(job.baseline_score, job.target)} to ${formatHoldoutScore(job.optimized_score, job.target)} (${job.optimized_score >= job.baseline_score ? '+' : ''}${holdoutPoints(job.optimized_score - job.baseline_score, job.target)} pts).`
                        : 'Awaiting holdout evaluation results.'}
                    </p>
                  </div>
                </div>
                <Badge variant={allGatesPassed && job.status === 'completed' ? 'default' : 'destructive'}>
                  {job.saved === true ? 'Artifact saved' : job.saved === false ? 'Not saved' : job.status}
                </Badge>
              </div>

              {/* Gate Checklist Table */}
              <div className="flex flex-col gap-2">
                <h3
                  className={typeClass('subtitle')}
                  style={{ fontVariationSettings: fontWeights.semibold }}
                >
                  Promotion gate checklist
                </h3>
                {gateChecks.length === 0 ? (
                  <p className={`${typeClass('body')} rounded-lg border p-3 text-muted-foreground`}>
                    No gate checks recorded for this run yet.
                  </p>
                ) : (
                  <div className="overflow-x-auto rounded-xl border">
                    <table className="w-full border-collapse text-left">
                      <thead>
                        <tr className="border-b bg-muted/40">
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            Gate rule
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            Verdict
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                            Actual
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                            Threshold
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {gateChecks.map((check) => (
                          <tr key={check.name} data-testid={`gate-check-${check.name}`}>
                            <td className="px-3 py-2.5 align-top">
                              <p
                                className={`${typeClass('body')} text-foreground`}
                                style={{ fontVariationSettings: fontWeights.semibold }}
                              >
                                {check.label}
                              </p>
                              {check.reason && (
                                <p className={`${typeClass('caption')} mt-0.5 text-muted-foreground`}>
                                  {check.reason}
                                </p>
                              )}
                            </td>
                            <td className="px-3 py-2.5 align-top">
                              <span
                                className={`${typeClass('caption')} inline-flex items-center gap-1 font-medium ${check.passed ? 'text-foreground' : 'text-destructive'}`}
                              >
                                {check.passed ? (
                                  <CheckCircle2 className="size-3.5 text-success" aria-hidden="true" />
                                ) : (
                                  <XCircle className="size-3.5 text-destructive" aria-hidden="true" />
                                )}
                                {check.passed ? 'Pass' : 'Fail'}
                              </span>
                            </td>
                            <td className={`${typeClass('caption')} px-3 py-2.5 text-right font-mono tabular-nums text-foreground`}>
                              {formatActualValue(check.actual)}
                            </td>
                            <td className={`${typeClass('caption')} px-3 py-2.5 text-right font-mono tabular-nums text-muted-foreground`}>
                              {formatActualValue(check.threshold)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Execution Stage Timeline */}
              <div className="flex flex-col gap-2">
                <h3
                  className={typeClass('subtitle')}
                  style={{ fontVariationSettings: fontWeights.semibold }}
                >
                  Execution stage timeline
                </h3>
                {timeline.length === 0 ? (
                  <p className={`${typeClass('body')} rounded-lg border p-3 text-muted-foreground`}>
                    No stage timing records were captured for this run.
                  </p>
                ) : (
                  <div className="overflow-x-auto rounded-xl border">
                    <table className="w-full border-collapse text-left">
                      <thead>
                        <tr className="border-b bg-muted/40">
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            Stage
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            Status
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                            Duration
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {timeline.map((entry, idx) => (
                          <tr key={`${entry.stage}-${idx}`}>
                            <td className={`${typeClass('body')} px-3 py-2 text-foreground`}>
                              {entry.label || entry.stage}
                            </td>
                            <td className="px-3 py-2">
                              <Badge
                                variant={
                                  entry.status === 'failed'
                                    ? 'destructive'
                                    : entry.status === 'completed'
                                      ? 'secondary'
                                      : 'outline'
                                }
                              >
                                {entry.status ?? 'completed'}
                              </Badge>
                            </td>
                            <td className={`${typeClass('caption')} px-3 py-2 text-right font-mono tabular-nums text-foreground`}>
                              <span className="inline-flex items-center gap-1">
                                <Clock className="size-3 text-muted-foreground" aria-hidden="true" />
                                {formatDuration(entry.duration_seconds)}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </TabsPanel>

          {/* Tab 1: What Changed (Diffs + Calibration / Trajectory) */}
          <TabsPanel index={SHEET_TABS.diff} selectedIndex={activeTab} idPrefix="opt-diff-tabs">
            <div className="flex flex-col gap-5">
              {/* Before -> After Calibration / Metrics Matrix */}
              {calibrationRows.length > 0 && (
                <div className="flex flex-col gap-2">
                  <h3
                    className={typeClass('subtitle')}
                    style={{ fontVariationSettings: fontWeights.semibold }}
                  >
                    {job.target === 'rlm'
                      ? 'Before → After holdout metrics'
                      : 'Before → After calibration matrix'}
                  </h3>
                  <div className="overflow-x-auto rounded-xl border">
                    <table className="w-full border-collapse text-left">
                      <thead>
                        <tr className="border-b bg-muted/40">
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            Metric
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                            Baseline
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                            Candidate
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                            Delta
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {calibrationRows.map((row) => {
                          const d = formatMatrixDelta(
                            row.before,
                            row.after,
                            row.lowerBetter,
                            row.isCount,
                          )
                          return (
                            <tr key={row.label}>
                              <td className={`${typeClass('body')} px-3 py-2 text-foreground`}>
                                {row.label}
                              </td>
                              <td className={`${typeClass('body')} px-3 py-2 text-right font-mono tabular-nums text-muted-foreground`}>
                                {formatMatrixValue(row.before, row.isCount)}
                              </td>
                              <td className={`${typeClass('body')} px-3 py-2 text-right font-mono tabular-nums text-foreground`}>
                                {formatMatrixValue(row.after, row.isCount)}
                              </td>
                              <td className="px-3 py-2 text-right font-mono tabular-nums">
                                <span
                                  className={`${typeClass('caption')} ${
                                    d.tone === 'good'
                                      ? 'text-foreground font-medium'
                                      : d.tone === 'bad'
                                        ? 'text-destructive font-medium'
                                        : 'text-muted-foreground'
                                  }`}
                                >
                                  {d.text}
                                </span>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* ReAnchor Parameter Diff Table */}
              {job.target !== 'rlm' && (
                <div className="flex flex-col gap-2">
                  <h3
                    className={typeClass('subtitle')}
                    style={{ fontVariationSettings: fontWeights.semibold }}
                  >
                    ReAnchor parameter changes
                  </h3>
                  {parameterDiffs.length === 0 ? (
                    <p className={`${typeClass('body')} rounded-lg border p-3 text-muted-foreground`}>
                      No fitted threshold or weight changes recorded.
                    </p>
                  ) : (
                    <div className="overflow-x-auto rounded-xl border" data-testid="parameter-diffs-table">
                      <table className="w-full border-collapse text-left">
                        <thead>
                          <tr className="border-b bg-muted/40">
                            <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                              Gate
                            </th>
                            <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                              Parameter
                            </th>
                            <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                              Baseline
                            </th>
                            <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                              Fitted
                            </th>
                            <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                              Delta
                            </th>
                          </tr>
                        </thead>
                        <tbody className="divide-y">
                          {parameterDiffs.map((row) => {
                            const magnitude = Math.min(100, Math.round(Math.abs(row.delta) * 100))
                            return (
                              <tr key={`${row.gate}:${row.parameter}`}>
                                <td className={`${typeClass('body')} px-3 py-2 font-mono text-foreground`}>
                                  {row.gate}
                                </td>
                                <td className={`${typeClass('caption')} px-3 py-2 font-mono text-muted-foreground`}>
                                  {row.parameter}
                                </td>
                                <td className={`${typeClass('body')} px-3 py-2 text-right font-mono tabular-nums text-muted-foreground`}>
                                  {formatNumber(row.baseline)}
                                </td>
                                <td className={`${typeClass('body')} px-3 py-2 text-right font-mono tabular-nums text-foreground`}>
                                  {formatNumber(row.fitted)}
                                </td>
                                <td className="px-3 py-2 text-right">
                                  <div className="inline-flex items-center justify-end gap-2">
                                    <span
                                      aria-hidden="true"
                                      className="hidden h-1.5 w-14 overflow-hidden rounded-full bg-muted sm:inline-block"
                                    >
                                      <span
                                        className="block h-full rounded-full bg-foreground/70"
                                        style={{ width: `${Math.max(4, magnitude)}%` }}
                                      />
                                    </span>
                                    <Badge
                                      variant={Math.abs(row.delta) > 1e-6 ? 'secondary' : 'outline'}
                                      className="font-mono tabular-nums"
                                    >
                                      {row.delta >= 0 ? '+' : ''}
                                      {row.delta.toFixed(3)}
                                    </Badge>
                                  </div>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {/* GEPA Unified Instruction Diffs */}
              {job.target === 'rlm' && (
                <div className="flex flex-col gap-3">
                  <h3
                    className={typeClass('subtitle')}
                    style={{ fontVariationSettings: fontWeights.semibold }}
                  >
                    Prompt &amp; instruction diffs
                  </h3>
                  {predictorNames.length === 0 ? (
                    <p className={`${typeClass('body')} rounded-lg border p-3 text-muted-foreground`}>
                      No predictor instruction diffs recorded for this run.
                    </p>
                  ) : (
                    predictorNames.map((name) => (
                      <PredictorDiffCard
                        key={name}
                        name={name}
                        diffText={
                          typeof instructionDiffs?.[name] === 'string'
                            ? String(instructionDiffs[name])
                            : ''
                        }
                        beforeText={
                          typeof baseInstructions?.[name] === 'string'
                            ? String(baseInstructions[name])
                            : ''
                        }
                        afterText={
                          typeof optInstructions?.[name] === 'string'
                            ? String(optInstructions[name])
                            : ''
                        }
                      />
                    ))
                  )}
                </div>
              )}

              {/* GEPA Evolution Trajectory */}
              {job.target === 'rlm' && candidateHistory.length > 0 && (
                <div className="flex flex-col gap-2">
                  <h3
                    className={typeClass('subtitle')}
                    style={{ fontVariationSettings: fontWeights.semibold }}
                  >
                    GEPA evolution trajectory
                  </h3>
                  <div className="overflow-x-auto rounded-xl border" data-testid="candidate-history-table">
                    <table className="w-full border-collapse text-left">
                      <thead>
                        <tr className="border-b bg-muted/40">
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            #
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            Parent
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                            Val score
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 text-right font-medium text-muted-foreground`}>
                            Calls
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            Status
                          </th>
                          <th className={`${typeClass('caption')} px-3 py-2 font-medium text-muted-foreground`}>
                            Reflection summary
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {candidateHistory.map((row) => (
                          <tr key={row.candidate_idx}>
                            <td className={`${typeClass('caption')} px-3 py-2 font-mono tabular-nums text-foreground`}>
                              #{row.candidate_idx}
                            </td>
                            <td className={`${typeClass('caption')} px-3 py-2 font-mono tabular-nums text-muted-foreground`}>
                              {row.parent !== null ? `#${row.parent}` : 'seed'}
                            </td>
                            <td className={`${typeClass('caption')} px-3 py-2 text-right font-mono tabular-nums text-foreground`}>
                              {formatScore(row.val_score, true)}
                            </td>
                            <td className={`${typeClass('caption')} px-3 py-2 text-right font-mono tabular-nums text-muted-foreground`}>
                              {row.metric_calls ?? '—'}
                            </td>
                            <td className="px-3 py-2">
                              <div className="flex flex-wrap gap-1">
                                {row.is_best && <Badge variant="default">Best</Badge>}
                                <Badge variant={row.accepted ? 'secondary' : 'outline'}>
                                  {row.accepted ? 'Accepted' : 'Rejected'}
                                </Badge>
                              </div>
                            </td>
                            <td className={`${typeClass('caption')} max-w-xs px-3 py-2 text-muted-foreground`}>
                              {row.reflection_summary || '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </TabsPanel>

          {/* Tab 2: Per-Example Holdout Impact */}
          <TabsPanel index={SHEET_TABS.cases} selectedIndex={activeTab} idPrefix="opt-diff-tabs">
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className={`${typeClass('caption')} text-muted-foreground`}>
                  Blocking failures and regressions are pinned first.
                </p>
                <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter holdout cases">
                  <Button
                    type="button"
                    size="xs"
                    variant={caseFilter === 'all' ? 'default' : 'outline'}
                    onClick={() => setCaseFilter('all')}
                  >
                    All ({caseDeltas.length})
                  </Button>
                  <Button
                    type="button"
                    size="xs"
                    variant={caseFilter === 'regressed' ? 'default' : 'outline'}
                    onClick={() => setCaseFilter('regressed')}
                  >
                    Regressions ({regressedCount})
                  </Button>
                  <Button
                    type="button"
                    size="xs"
                    variant={caseFilter === 'fixed' ? 'default' : 'outline'}
                    onClick={() => setCaseFilter('fixed')}
                  >
                    Fixed ({fixedCount})
                  </Button>
                  {job.target === 'rlm' && (
                    <Button
                      type="button"
                      size="xs"
                      variant={caseFilter === 'blocking' ? 'default' : 'outline'}
                      onClick={() => setCaseFilter('blocking')}
                    >
                      Blocking ({blockingCount})
                    </Button>
                  )}
                </div>
              </div>

              {filteredCases.length === 0 ? (
                <p className={`${typeClass('body')} rounded-lg border p-4 text-muted-foreground`}>
                  No holdout case deltas match this filter.
                </p>
              ) : (
                <div className="flex flex-col gap-2.5" data-testid="case-deltas-list">
                  {filteredCases.map((item) => (
                    <div
                      key={item.case_id}
                      className="flex flex-col gap-2 rounded-xl border bg-card p-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <code className={`${typeClass('caption')} font-mono text-foreground`}>
                            {item.case_id}
                          </code>
                          {item.lineage && <Badge variant="outline">{item.lineage}</Badge>}
                          {item.blocking_failed && <Badge variant="destructive">Blocking failure</Badge>}
                          {item.paired_regression && (
                            <Badge variant="destructive">Paired regression</Badge>
                          )}
                          {!item.blocking_failed && !item.paired_regression && (
                            <Badge
                              variant={
                                item.outcome === 'regressed'
                                  ? 'destructive'
                                  : item.outcome === 'fixed'
                                    ? 'default'
                                    : 'secondary'
                              }
                            >
                              {item.outcome === 'fixed'
                                ? 'Fixed'
                                : item.outcome === 'regressed'
                                  ? 'Regressed'
                                  : 'Unchanged'}
                            </Badge>
                          )}
                        </div>
                        <span className={`${typeClass('caption')} font-mono tabular-nums text-foreground`}>
                          {formatScore(item.baseline_score, job.target === 'rlm')} →{' '}
                          {formatScore(item.candidate_score, job.target === 'rlm')}
                          {item.delta !== null && (
                            <span className="ml-1.5 text-muted-foreground">
                              ({item.delta >= 0 ? '+' : ''}
                              {job.target === 'rlm' && Math.abs(item.delta) > 1
                                ? item.delta.toFixed(1)
                                : `${(item.delta * 100).toFixed(1)} pts`}
                              )
                            </span>
                          )}
                        </span>
                      </div>

                      {item.input_preview && (
                        <p className={`${typeClass('body')} text-foreground`}>{item.input_preview}</p>
                      )}

                      <dl className="grid grid-cols-1 gap-2 border-t border-border/60 pt-2 sm:grid-cols-3">
                        {item.expected !== undefined && (
                          <div>
                            <dt className={`${typeClass('caption')} text-muted-foreground`}>
                              Expected
                            </dt>
                            <dd className={`${typeClass('caption')} font-mono text-foreground`}>
                              {typeof item.expected === 'string'
                                ? item.expected
                                : JSON.stringify(item.expected)}
                            </dd>
                          </div>
                        )}
                        {(item.baseline_pred !== undefined || item.baseline_pass_rate !== null) && (
                          <div>
                            <dt className={`${typeClass('caption')} text-muted-foreground`}>
                              Baseline
                            </dt>
                            <dd className={`${typeClass('caption')} font-mono text-muted-foreground`}>
                              {item.baseline_pred !== undefined
                                ? `${String(item.baseline_pred)}${
                                    item.baseline_prob !== null && item.baseline_prob !== undefined
                                      ? ` (p=${item.baseline_prob.toFixed(2)})`
                                      : ''
                                  }`
                                : `pass ${formatScore(item.baseline_pass_rate)} · corr ${formatScore(item.baseline_correctness)}`}
                            </dd>
                          </div>
                        )}
                        {(item.candidate_pred !== undefined || item.candidate_pass_rate !== null) && (
                          <div>
                            <dt className={`${typeClass('caption')} text-muted-foreground`}>
                              Candidate
                            </dt>
                            <dd className={`${typeClass('caption')} font-mono text-foreground`}>
                              {item.candidate_pred !== undefined
                                ? `${String(item.candidate_pred)}${
                                    item.candidate_prob !== null && item.candidate_prob !== undefined
                                      ? ` (p=${item.candidate_prob.toFixed(2)})`
                                      : ''
                                  }`
                                : `pass ${formatScore(item.candidate_pass_rate)} · corr ${formatScore(item.candidate_correctness)}`}
                            </dd>
                          </div>
                        )}
                      </dl>

                      {(item.baseline_feedback || item.candidate_feedback) && (
                        <div className="flex flex-col gap-1 border-t border-border/60 pt-2">
                          {item.baseline_feedback && (
                            <p className={`${typeClass('caption')} text-muted-foreground`}>
                              <strong>Baseline note:</strong> {item.baseline_feedback}
                            </p>
                          )}
                          {item.candidate_feedback && (
                            <p className={`${typeClass('caption')} text-foreground`}>
                              <strong>Candidate note:</strong> {item.candidate_feedback}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </TabsPanel>

          {/* Tab 3: Logs & Provenance */}
          <TabsPanel index={SHEET_TABS.logs} selectedIndex={activeTab} idPrefix="opt-diff-tabs">
            <div className="flex flex-col gap-4">
              {/* Models Provenance */}
              {modelsProvenance.length > 0 && (
                <div className="flex flex-col gap-2 rounded-xl border bg-card p-3.5">
                  <h3
                    className={typeClass('subtitle')}
                    style={{ fontVariationSettings: fontWeights.semibold }}
                  >
                    Models &amp; provider provenance
                  </h3>
                  <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {modelsProvenance.map((row) => (
                      <div key={row.role} className="rounded-lg border p-2.5">
                        <dt className={`${typeClass('caption')} text-muted-foreground`}>
                          {row.role}
                        </dt>
                        <dd className={`${typeClass('body')} mt-0.5 wrap-anywhere font-mono text-foreground`}>
                          {row.model}
                        </dd>
                        {(row.providerName || row.dialect) && (
                          <dd className="mt-1 flex flex-wrap gap-1">
                            {row.providerName && (
                              <Badge variant="secondary">{row.providerName}</Badge>
                            )}
                            {row.dialect && <Badge variant="outline">{row.dialect}</Badge>}
                          </dd>
                        )}
                      </div>
                    ))}
                  </dl>
                </div>
              )}

              {/* Artifact & Tracking Metadata */}
              <div className="flex flex-col gap-2 rounded-xl border bg-card p-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3
                    className={typeClass('subtitle')}
                    style={{ fontVariationSettings: fontWeights.semibold }}
                  >
                    Artifact &amp; run metadata
                  </h3>
                  {safeMlflowUrl && (
                    <Button
                      variant="outline"
                      size="xs"
                      nativeButton={false}
                      render={<a href={safeMlflowUrl} target="_blank" rel="noreferrer" />}
                    >
                      <ArrowUpRight className="size-3.5" aria-hidden="true" />
                      Open in MLflow
                    </Button>
                  )}
                </div>
                <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <div>
                    <dt className={`${typeClass('caption')} text-muted-foreground`}>Job ID</dt>
                    <dd className={`${typeClass('caption')} font-mono text-foreground`}>{job.id}</dd>
                  </div>
                  <div>
                    <dt className={`${typeClass('caption')} text-muted-foreground`}>
                      Dataset / Program
                    </dt>
                    <dd className={`${typeClass('caption')} text-foreground`}>
                      {job.dataset_name ?? job.dataset_source}
                      {job.program_kind ? ` · dspy.${job.program_kind === 'flex' ? 'Flex' : 'RLM'}` : ''}
                    </dd>
                  </div>
                  {job.artifact_sha256 && (
                    <div className="sm:col-span-2">
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>
                        Artifact SHA-256
                      </dt>
                      <dd className={`${typeClass('caption')} break-all font-mono text-foreground`}>
                        {job.artifact_sha256}
                      </dd>
                    </div>
                  )}
                </dl>
              </div>

              {/* Run output tail */}
              <CodeBlock size="sm">
                <CodeBlockHeader>
                  <CodeBlockTitle>Run output tail</CodeBlockTitle>
                  <CodeBlockActions>
                    <Tooltip>
                      <TooltipTrigger render={<span className="inline-flex" />}>
                        <CodeBlockCopyButton
                          value={job.log_tail || 'No optimizer logs captured.'}
                          aria-label="Copy run logs"
                        />
                      </TooltipTrigger>
                      <TooltipContent side="top">Copy run logs</TooltipContent>
                    </Tooltip>
                  </CodeBlockActions>
                </CodeBlockHeader>
                <CodeBlockContent
                  wrap
                  className="max-h-96 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
                >
                  {job.log_tail || 'No optimizer output logs recorded.'}
                </CodeBlockContent>
              </CodeBlock>
            </div>
          </TabsPanel>
        </div>
      </ScrollArea>
    </div>
  )
}

export function RunFullDiffSheet({
  job,
  open,
  onOpenChange,
}: {
  job: OptimizationJobDetail | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const titleTarget =
    job?.target === 'skills'
      ? 'Skill Selector'
      : job?.target === 'memory'
        ? 'Memory Gate'
        : 'Root RLM'

  return (
    <Sheet open={open && Boolean(job)} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full gap-0 overflow-hidden p-0 data-[side=right]:sm:max-w-4xl"
      >
        <SheetHeader className="border-b px-4 py-3.5 pr-12">
          <SheetTitle>
            {job ? `${titleTarget} — Full Diff & Diagnostics` : 'Full Diff & Diagnostics'}
          </SheetTitle>
          <SheetDescription>
            {job
              ? `${job.dataset_name ?? job.dataset_source} · Inspect gate checks, parameter or instruction diffs, per-example holdout impact, and provenance.`
              : 'Inspect run diagnostics.'}
          </SheetDescription>
        </SheetHeader>
        {job && <RunFullDiffContent key={job.id} job={job} />}
      </SheetContent>
    </Sheet>
  )
}
