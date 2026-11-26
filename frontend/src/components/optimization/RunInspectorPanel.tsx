import { asRecord, buildModelsProvenance, parseGateChecks } from './optimization-report'
import { formatScore, holdoutPoints } from './holdout-score'
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Clock,
  FileDiff,
  Play,
  RotateCcw,
  Sparkles,
  XCircle,
} from 'lucide-react'
import { useMemo, useState } from 'react'
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
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  errorMessage,
  type OptimizationJobDetail,
  type OptimizationJobSummary,
  type TargetStatusSummary,
} from '@/lib/api'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

function formatRate(value: unknown): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function RunInspectorPanel({
  job,
  loading,
  activePromotionJobId,
  activeTargetSummary = null,
  onPromote,
  onRerun,
  onOpenFullDiff,
}: {
  job: OptimizationJobDetail | null
  loading: boolean
  activePromotionJobId: string | null
  activeTargetSummary?: TargetStatusSummary | null
  onPromote: (
    jobId: string,
    opts: { confirm_non_qualifying: boolean; pin_decision_model: boolean },
  ) => Promise<void>
  onRerun: (job: OptimizationJobSummary) => void
  onOpenFullDiff?: (job: OptimizationJobDetail) => void
}) {
  const [promoteOpen, setPromoteOpen] = useState(false)
  const [confirmNonQualifying, setConfirmNonQualifying] = useState(false)
  const [pinDecisionModel, setPinDecisionModel] = useState(true)
  const [promoting, setPromoting] = useState(false)
  const [promoteError, setPromoteError] = useState('')

  const report = useMemo(() => asRecord(job?.report_json), [job?.report_json])
  const gateChecklist = useMemo(
    () => (job ? parseGateChecks(job) : []),
    [job],
  )
  const provenanceModels = useMemo(
    () => (job ? buildModelsProvenance(job) : []),
    [job],
  )

  if (!job && loading) {
    return (
      <div
        className="flex flex-col gap-4 rounded-xl border bg-card p-4"
        role="status"
        aria-label="Loading run details"
      >
        <Skeleton className="h-6 w-48 bg-hover motion-reduce:animate-none" />
        <div className="grid grid-cols-2 gap-3">
          <Skeleton className="h-16 w-full bg-hover motion-reduce:animate-none" />
          <Skeleton className="h-16 w-full bg-hover motion-reduce:animate-none" />
        </div>
        <Skeleton className="h-32 w-full bg-hover motion-reduce:animate-none" />
      </div>
    )
  }

  if (!job) {
    return (
      <div className="flex flex-col items-start gap-2 rounded-xl border bg-card p-5">
        <h3 className={typeClass('subtitle')} style={{ fontVariationSettings: fontWeights.semibold }}>
          Run Inspector
        </h3>
        <p className={`${typeClass('body')} text-muted-foreground`}>
          Select an optimization run from the table to inspect gate verdicts, holdout deltas,
          parameter or prompt diffs, logs, and promotion controls.
        </p>
      </div>
    )
  }

  const targetTitle =
    job.target === 'skills'
      ? 'Skill Selector'
      : job.target === 'memory'
        ? 'Memory Gate'
        : 'Root RLM'

  const programKind =
    job.program_kind ??
    (typeof job.config_json?.program_kind === 'string' ? job.config_json.program_kind : null)
  const isFlexEvaluationOnly = job.target === 'rlm' && programKind === 'flex'
  const requiresNonQualifyingConfirm = job.saved === false || report?.eligible !== true
  const isQualifying = !requiresNonQualifyingConfirm
  const isCurrentlyPromoted = activePromotionJobId === job.id

  const canPromote =
    job.status === 'completed' &&
    !isFlexEvaluationOnly &&
    Boolean(job.artifact_object_key) &&
    Boolean(job.artifact_sha256) &&
    !isCurrentlyPromoted

  const promoteDisabledReason = isCurrentlyPromoted
    ? null
    : isFlexEvaluationOnly
      ? 'dspy.Flex runs are evaluation-only; chat serving requires dspy.RLM artifacts.'
      : job.status === 'cancelled' || job.status === 'failed'
        ? 'Re-run this evaluation to produce a candidate for promotion.'
      : job.status !== 'completed'
        ? 'Wait for the run to complete before promoting.'
        : !job.artifact_object_key || !job.artifact_sha256
          ? 'No artifact was saved because the automatic quality gate rejected this candidate (re-run with --force to save non-qualifying artifacts).'
          : null

  const fpBefore = report?.fp_rate_before
  const fpAfter = report?.fp_rate_after
  const diagAfter = asRecord(report?.diagnostics_after)
  const fittedParams = asRecord(report?.fitted_parameters)
  const paramDiffs = Array.isArray(report?.parameter_diffs) ? report.parameter_diffs : null
  const rlmMetrics = asRecord(report?.metrics)
  const baseInstr = asRecord(report?.baseline_instructions)
  const optInstr = asRecord(report?.optimized_instructions)
  const instrDiffs = asRecord(report?.instruction_diffs)

  const changedParamCount = paramDiffs
    ? paramDiffs.filter((item) => {
        const rec = asRecord(item)
        return typeof rec?.delta === 'number' && Math.abs(rec.delta) > 1e-6
      }).length
    : fittedParams
      ? Object.keys(fittedParams).length
      : 0

  const modifiedPredictorCount = instrDiffs
    ? Object.values(instrDiffs).filter((diff) => typeof diff === 'string' && diff.trim().length > 0)
        .length
    : baseInstr && optInstr
      ? Object.entries(optInstr).filter(
          ([key, val]) => String(val ?? '') !== String(baseInstr[key] ?? ''),
        ).length
      : 0

  const delta =
    job.baseline_score !== null && job.optimized_score !== null
      ? job.optimized_score - job.baseline_score
      : null

  const safeMlflowUrl =
    job.mlflow_run_url && /^https?:\/\//i.test(job.mlflow_run_url) ? job.mlflow_run_url : null

  const verdict = (() => {
    if (job.status === 'failed') {
      return {
        kind: 'fail' as const,
        title: 'Run failed',
        subtitle: job.error || 'The optimizer exited with an error before completing holdout eval.',
      }
    }
    if (job.status === 'cancelled') {
      return {
        kind: 'warn' as const,
        title: 'Run cancelled',
        subtitle: 'Stopped before promotion. No active runtime artifacts were changed.',
      }
    }
    if (job.status === 'running' || job.status === 'queued') {
      return {
        kind: 'pending' as const,
        title: job.status === 'queued' ? 'Run queued' : 'Optimization in progress',
        subtitle: job.progress_json?.label || job.stage,
      }
    }
    if (isQualifying) {
      return {
        kind: 'pass' as const,
        title: 'Passed all promotion gates',
        subtitle: isFlexEvaluationOnly
          ? 'Passed holdout gates (dspy.Flex evaluation-only run).'
          : 'Candidate improved holdout score without regressions and is ready to promote.',
      }
    }
    return {
      kind: 'fail' as const,
      title: 'Gate rejected candidate',
      subtitle:
        job.saved === true
          ? 'Saved via --force even though one or more quality gates failed.'
          : 'Candidate did not satisfy all promotion gates; live traffic remains unchanged.',
    }
  })()

  const candidateDecisionEntry = provenanceModels.find((item) => item.role === 'Decision LM')
  const activePromo = activeTargetSummary?.active_promotion ?? null

  return (
    <>
      <div
        className="flex min-w-0 flex-col gap-4 rounded-xl border bg-card p-4"
        data-testid="run-inspector"
      >
        {/* Header & actions */}
        <div className="flex flex-wrap items-start justify-between gap-2 border-b border-border/60 pb-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3
                className={typeClass('subtitle')}
                style={{ fontVariationSettings: fontWeights.semibold }}
              >
                {targetTitle}
              </h3>
              <Badge
                variant={
                  job.status === 'completed'
                    ? 'secondary'
                    : job.status === 'failed'
                      ? 'destructive'
                      : job.status === 'running'
                        ? 'default'
                        : 'outline'
                }
              >
                {job.status}
              </Badge>
              {isFlexEvaluationOnly && <Badge variant="outline">dspy.Flex · eval only</Badge>}
              {isCurrentlyPromoted && <Badge variant="default">Active in runtime</Badge>}
            </div>
            <p className={`${typeClass('caption')} mt-1 text-muted-foreground`}>
              {job.dataset_name ?? job.dataset_source} · Started{' '}
              {formatTimestamp(job.started_at ?? job.created_at)}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {safeMlflowUrl && (
              <Button
                variant="outline"
                size="sm"
                nativeButton={false}
                render={<a href={safeMlflowUrl} target="_blank" rel="noreferrer" />}
              >
                <ArrowUpRight className="size-3.5" aria-hidden="true" />
                MLflow
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={() => onRerun(job)}>
              <RotateCcw className="size-3.5" aria-hidden="true" />
              Re-run
            </Button>
            <Button
              size="sm"
              disabled={!canPromote}
              onClick={() => {
                setConfirmNonQualifying(false)
                setPinDecisionModel(true)
                setPromoteError('')
                setPromoteOpen(true)
              }}
              data-testid="promote-run-button"
            >
              <Sparkles className="size-3.5" aria-hidden="true" />
              {isCurrentlyPromoted ? 'Promoted' : 'Promote'}
            </Button>
          </div>
        </div>

        {/* Verdict Banner */}
        <div
          className={`flex items-start gap-2.5 rounded-lg border p-3 ${
            verdict.kind === 'fail'
              ? 'border-destructive/30 bg-destructive/5'
              : 'border-border bg-muted/20'
          }`}
          data-testid="run-verdict-banner"
        >
          {verdict.kind === 'pass' ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-hidden="true" />
          ) : verdict.kind === 'fail' ? (
            <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
          ) : verdict.kind === 'warn' ? (
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
          ) : (
            <Clock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          )}
          <div className="min-w-0 flex-1">
            <p
              className={`${typeClass('body')} text-foreground`}
              style={{ fontVariationSettings: fontWeights.semibold }}
            >
              {verdict.title}
            </p>
            <p className={`${typeClass('caption')} mt-0.5 text-muted-foreground`}>
              {verdict.subtitle}
            </p>
          </div>
        </div>

        {promoteDisabledReason && (
          <p className={`${typeClass('caption')} text-muted-foreground`} data-testid="promote-disabled-reason">
            {promoteDisabledReason}
          </p>
        )}

        {/* 2x2 KPI metrics */}
        <dl className="grid grid-cols-2 gap-3">
          <div className="rounded-lg border p-3">
            <dt className={`${typeClass('caption')} text-muted-foreground`}>Holdout score</dt>
            <dd
              className={`${typeClass('title')} tabular-nums text-foreground`}
              style={{ fontVariationSettings: fontWeights.semibold }}
            >
              {formatScore(job.baseline_score, job.target)} → {formatScore(job.optimized_score, job.target)}
            </dd>
            {delta !== null && (
              <dd className={`${typeClass('caption')} tabular-nums text-muted-foreground`}>
                {delta >= 0 ? '+' : ''}
                {holdoutPoints(delta, job.target)} pts vs baseline
              </dd>
            )}
          </div>
          <div className="rounded-lg border p-3">
            <dt className={`${typeClass('caption')} text-muted-foreground`}>Dataset split</dt>
            <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
              {job.train_size ?? '—'} train
              {job.val_size ? ` · ${job.val_size} val` : ''} · {job.holdout_size ?? '—'} holdout
            </dd>
          </div>
          <div className="rounded-lg border p-3">
            <dt className={`${typeClass('caption')} text-muted-foreground`}>Gate verdict</dt>
            <dd className={`${typeClass('body')} flex items-center gap-1.5 text-foreground`}>
              {verdict.kind === 'pass' ? (
                <CheckCircle2 className="size-3.5 shrink-0 text-success" aria-hidden="true" />
              ) : verdict.kind === 'fail' ? (
                <XCircle className="size-3.5 shrink-0 text-destructive" aria-hidden="true" />
              ) : verdict.kind === 'warn' ? (
                <AlertTriangle className="size-3.5 shrink-0 text-warning" aria-hidden="true" />
              ) : (
                <Clock className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              )}
              <span>
                {job.status !== 'completed'
                  ? job.stage
                  : isQualifying
                    ? 'Qualified'
                    : 'Rejected'}
              </span>
            </dd>
          </div>
          <div className="rounded-lg border p-3">
            <dt className={`${typeClass('caption')} text-muted-foreground`}>
              {job.target === 'rlm' ? 'Modified predictors' : 'Calibrated parameters'}
            </dt>
            <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
              {job.target === 'rlm'
                ? `${modifiedPredictorCount} predictor${modifiedPredictorCount === 1 ? '' : 's'}`
                : `${changedParamCount} parameter${changedParamCount === 1 ? '' : 's'}`}
            </dd>
          </div>
        </dl>

        {/* Open Full Diff & Diagnostics CTA */}
        {onOpenFullDiff && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenFullDiff(job)}
            className="w-full justify-center"
            data-testid="open-full-diff-button"
          >
            <FileDiff className="size-3.5" aria-hidden="true" />
            Open Full Diff &amp; Diagnostics
          </Button>
        )}

        {/* Explicit Promotion Gate Checklist */}
        {gateChecklist.length > 0 && (
          <div
            className="flex flex-col gap-2 rounded-lg border p-3"
            data-testid="inspector-gate-checklist"
          >
            <h4
              className={typeClass('body')}
              style={{ fontVariationSettings: fontWeights.semibold }}
            >
              Promotion gate checklist
            </h4>
            <ul className="flex flex-col gap-2">
              {gateChecklist.map((check) => (
                <li key={check.name} className="flex items-start gap-2">
                  {check.passed ? (
                    <CheckCircle2
                      className="mt-0.5 size-3.5 shrink-0 text-success"
                      aria-hidden="true"
                    />
                  ) : (
                    <XCircle
                      className="mt-0.5 size-3.5 shrink-0 text-destructive"
                      aria-hidden="true"
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <p
                      className={`${typeClass('caption')} ${check.passed ? 'text-foreground' : 'text-destructive'}`}
                      style={{ fontVariationSettings: fontWeights.semibold }}
                    >
                      {check.label}
                    </p>
                    {check.reason && (
                      <p className={`${typeClass('caption')} text-muted-foreground`}>
                        {check.reason}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Models & Provider Provenance */}
        {provenanceModels.length > 0 && (
          <div className="flex flex-col gap-2 rounded-lg border p-3">
            <h4
              className={typeClass('body')}
              style={{ fontVariationSettings: fontWeights.semibold }}
            >
              Models &amp; provider provenance
            </h4>
            <dl className="grid grid-cols-1 gap-1.5">
              {provenanceModels.map((entry) => (
                <div
                  key={entry.role}
                  className={`${typeClass('caption')} flex items-baseline justify-between gap-2`}
                >
                  <dt className="shrink-0 text-muted-foreground">{entry.role}</dt>
                  <dd className="min-w-0 truncate font-mono text-foreground" title={entry.model}>
                    {entry.providerName ? `${entry.providerName} · ${entry.model}` : entry.model}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {job.error && (
          <Alert variant="destructive">
            <AlertDescription>{job.error}</AlertDescription>
          </Alert>
        )}

        {/* Quick ReAnchor summary */}
        {job.target !== 'rlm' && (fpBefore !== undefined || diagAfter) && (
          <div className="flex flex-col gap-2 rounded-lg border p-3">
            <h4
              className={typeClass('body')}
              style={{ fontVariationSettings: fontWeights.semibold }}
            >
              Calibration snapshot
            </h4>
            <dl className="grid grid-cols-2 gap-2">
              <div>
                <dt className={`${typeClass('caption')} text-muted-foreground`}>
                  False-positive rate
                </dt>
                <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                  {formatRate(fpBefore)} → {formatRate(fpAfter)}
                </dd>
              </div>
              {diagAfter && typeof diagAfter.fn_rate === 'number' && (
                <div>
                  <dt className={`${typeClass('caption')} text-muted-foreground`}>
                    False-negative rate
                  </dt>
                  <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                    {formatRate(diagAfter.fn_rate)}
                  </dd>
                </div>
              )}
              {diagAfter && typeof diagAfter.primary_accuracy === 'number' && (
                <div>
                  <dt className={`${typeClass('caption')} text-muted-foreground`}>
                    Primary skill accuracy
                  </dt>
                  <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                    {formatRate(diagAfter.primary_accuracy)}
                  </dd>
                </div>
              )}
              {diagAfter && typeof diagAfter.action_accuracy === 'number' && (
                <div>
                  <dt className={`${typeClass('caption')} text-muted-foreground`}>
                    Action / scope accuracy
                  </dt>
                  <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                    {formatRate(diagAfter.action_accuracy)} · {formatRate(diagAfter.scope_accuracy)}
                  </dd>
                </div>
              )}
            </dl>
          </div>
        )}

        {/* Quick RLM summary */}
        {job.target === 'rlm' && rlmMetrics && (
          <div className="flex flex-col gap-2 rounded-lg border p-3">
            <h4
              className={typeClass('body')}
              style={{ fontVariationSettings: fontWeights.semibold }}
            >
              RLM holdout snapshot
            </h4>
            <dl className="grid grid-cols-2 gap-2">
              <div>
                <dt className={`${typeClass('caption')} text-muted-foreground`}>Submit rate</dt>
                <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                  {formatRate(rlmMetrics.baseline_submit_rate)} →{' '}
                  {formatRate(rlmMetrics.optimized_submit_rate)}
                </dd>
              </div>
              <div>
                <dt className={`${typeClass('caption')} text-muted-foreground`}>Correctness</dt>
                <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                  {formatRate(rlmMetrics.baseline_correctness)} →{' '}
                  {formatRate(rlmMetrics.optimized_correctness)}
                </dd>
              </div>
            </dl>
          </div>
        )}

        {/* Compact fitted parameters preview (if present) */}
        {fittedParams && Object.keys(fittedParams).length > 0 && (
          <CodeBlock size="sm">
            <CodeBlockHeader>
              <CodeBlockTitle>Fitted ReAnchor thresholds &amp; weights</CodeBlockTitle>
              <CodeBlockActions>
                <Tooltip>
                  <TooltipTrigger render={<span className="inline-flex" />}>
                    <CodeBlockCopyButton
                      value={JSON.stringify(fittedParams, null, 2)}
                      aria-label="Copy fitted parameters"
                    />
                  </TooltipTrigger>
                  <TooltipContent side="top">Copy fitted parameters</TooltipContent>
                </Tooltip>
              </CodeBlockActions>
            </CodeBlockHeader>
            <CodeBlockContent
              wrap
              className="max-h-40 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
            >
              {JSON.stringify(fittedParams, null, 2)}
            </CodeBlockContent>
          </CodeBlock>
        )}

        {/* Artifact provenance */}
        {job.artifact_sha256 && (
          <div
            className={`${typeClass('caption')} flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-muted-foreground`}
          >
            <span>Artifact SHA-256</span>
            <code className="font-mono text-foreground">{job.artifact_sha256.slice(0, 16)}…</code>
          </div>
        )}

        {job.log_tail && (
          <CodeBlock size="sm">
            <CodeBlockHeader>
              <CodeBlockTitle>Run output tail</CodeBlockTitle>
              <CodeBlockActions>
                <Tooltip>
                  <TooltipTrigger render={<span className="inline-flex" />}>
                    <CodeBlockCopyButton value={job.log_tail} aria-label="Copy run logs" />
                  </TooltipTrigger>
                  <TooltipContent side="top">Copy run logs</TooltipContent>
                </Tooltip>
              </CodeBlockActions>
            </CodeBlockHeader>
            <CodeBlockContent
              wrap
              className="max-h-44 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
            >
              {job.log_tail}
            </CodeBlockContent>
          </CodeBlock>
        )}
      </div>

      {/* Confident Promote Dialog with side-by-side comparison & in-modal errors */}
      <AlertDialog
        open={promoteOpen}
        onOpenChange={(open) => {
          if (!open && !promoting) {
            setPromoteOpen(false)
            setPromoteError('')
          }
        }}
      >
        <AlertDialogContent className="sm:max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle>Promote {targetTitle} candidate to live traffic?</AlertDialogTitle>
            <AlertDialogDescription>
              Activates this verified SHA-256 artifact across all API workers immediately without
              restarting the server.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="flex flex-col gap-3">
            {/* Side-by-side Active -> Candidate comparison table */}
            <div className="overflow-x-auto rounded-lg border" data-testid="promote-comparison-table">
              <table className="w-full border-collapse text-left">
                <thead>
                  <tr className="border-b bg-muted/40">
                    <th className={`${typeClass('caption')} px-2.5 py-1.5 font-medium text-muted-foreground`}>
                      Attribute
                    </th>
                    <th className={`${typeClass('caption')} px-2.5 py-1.5 font-medium text-muted-foreground`}>
                      Currently active
                    </th>
                    <th className={`${typeClass('caption')} px-2.5 py-1.5 font-medium text-foreground`}>
                      Candidate
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  <tr>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 text-muted-foreground`}>
                      Source
                    </td>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 text-muted-foreground`}>
                      {activeTargetSummary?.source === 'promoted'
                        ? 'Promoted artifact'
                        : activeTargetSummary?.source === 'env'
                          ? 'Env artifact'
                          : 'Default baseline'}
                    </td>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 font-medium text-foreground`}>
                      Run {job.id.slice(0, 8)}
                    </td>
                  </tr>
                  <tr>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 text-muted-foreground`}>
                      Artifact SHA
                    </td>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 font-mono text-muted-foreground`}>
                      {activePromo?.artifact_sha256
                        ? activePromo.artifact_sha256.slice(0, 12)
                        : activeTargetSummary?.env_path || 'Built-in'}
                    </td>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 font-mono text-foreground`}>
                      {job.artifact_sha256 ? job.artifact_sha256.slice(0, 12) : '—'}
                    </td>
                  </tr>
                  <tr>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 text-muted-foreground`}>
                      Holdout score
                    </td>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 font-mono tabular-nums text-muted-foreground`}>
                      {formatScore(activePromo?.optimized_score ?? job.baseline_score, job.target)}
                    </td>
                    <td className={`${typeClass('caption')} px-2.5 py-1.5 font-mono tabular-nums text-foreground`}>
                      {formatScore(job.optimized_score, job.target)}
                    </td>
                  </tr>
                  {job.target !== 'rlm' && (fpBefore !== undefined || fpAfter !== undefined) && (
                    <tr>
                      <td className={`${typeClass('caption')} px-2.5 py-1.5 text-muted-foreground`}>
                        FP rate
                      </td>
                      <td className={`${typeClass('caption')} px-2.5 py-1.5 font-mono tabular-nums text-muted-foreground`}>
                        {formatRate(fpBefore)}
                      </td>
                      <td className={`${typeClass('caption')} px-2.5 py-1.5 font-mono tabular-nums text-foreground`}>
                        {formatRate(fpAfter)}
                      </td>
                    </tr>
                  )}
                  {job.target !== 'rlm' && (
                    <tr>
                      <td className={`${typeClass('caption')} px-2.5 py-1.5 text-muted-foreground`}>
                        Pinned model
                      </td>
                      <td className={`${typeClass('caption')} px-2.5 py-1.5 font-mono text-muted-foreground`}>
                        {activePromo?.decision_provider_name
                          ? `${activePromo.decision_provider_name} · ${activePromo.resolved_decision_model ?? 'default'}`
                          : (activePromo?.resolved_decision_model ?? 'Workspace default')}
                      </td>
                      <td className={`${typeClass('caption')} px-2.5 py-1.5 font-mono text-foreground`}>
                        {candidateDecisionEntry?.providerName
                          ? `${candidateDecisionEntry.providerName} · ${job.resolved_decision_model ?? candidateDecisionEntry.model}`
                          : (job.resolved_decision_model ?? 'Workspace default')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {requiresNonQualifyingConfirm && (
              <Alert variant="destructive">
                <AlertDescription className="flex flex-col gap-2">
                  <span>
                    This candidate did not pass every automatic holdout promotion gate. Only
                    promote if you explicitly intend to override the quality gate.
                  </span>
                  <Switch
                    label="I understand and confirm promoting a non-qualifying candidate"
                    checked={confirmNonQualifying}
                    onToggle={() => setConfirmNonQualifying((prev) => !prev)}
                  />
                </AlertDescription>
              </Alert>
            )}

            {job.target !== 'rlm' && job.resolved_decision_model && (
              <div className="rounded-lg border p-3">
                <Switch
                  label={`Pin decision model to ${job.resolved_decision_model}`}
                  checked={pinDecisionModel}
                  onToggle={() => setPinDecisionModel((prev) => !prev)}
                />
                <p className={`${typeClass('caption')} mt-1 text-muted-foreground`}>
                  Applies <strong>only to {targetTitle}</strong> — it does not change global
                  workspace settings or overwrite the other ReAnchor target&apos;s pinned model.
                </p>
              </div>
            )}

            {promoteError && (
              <Alert variant="destructive">
                <AlertDescription>{promoteError}</AlertDescription>
              </Alert>
            )}
          </div>

          <AlertDialogFooter>
            <AlertDialogCancel disabled={promoting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={promoting || (requiresNonQualifyingConfirm && !confirmNonQualifying)}
              onClick={async () => {
                setPromoteError('')
                setPromoting(true)
                try {
                  await onPromote(job.id, {
                    confirm_non_qualifying: requiresNonQualifyingConfirm && confirmNonQualifying,
                    pin_decision_model: pinDecisionModel,
                  })
                  setPromoteOpen(false)
                } catch (failure) {
                  setPromoteError(errorMessage(failure))
                } finally {
                  setPromoting(false)
                }
              }}
            >
              <Play className="size-3.5" aria-hidden="true" />
              {promoting ? 'Promoting…' : 'Promote live'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
