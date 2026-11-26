import { ArrowUpRight, CheckCircle2, Play, RotateCcw, Sparkles } from 'lucide-react'
import { useState } from 'react'
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
import type { OptimizationJobDetail, OptimizationJobSummary } from '@/lib/api'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

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

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

export function RunInspectorPanel({
  job,
  loading,
  activePromotionJobId,
  onPromote,
  onRerun,
}: {
  job: OptimizationJobDetail | null
  loading: boolean
  activePromotionJobId: string | null
  onPromote: (
    jobId: string,
    opts: { confirm_non_qualifying: boolean; pin_decision_model: boolean },
  ) => Promise<void>
  onRerun: (job: OptimizationJobSummary) => void
}) {
  const [promoteOpen, setPromoteOpen] = useState(false)
  const [confirmNonQualifying, setConfirmNonQualifying] = useState(false)
  const [pinDecisionModel, setPinDecisionModel] = useState(true)
  const [promoting, setPromoting] = useState(false)

  if (!job && loading) {
    return (
      <div className="flex flex-col gap-4 rounded-xl border bg-card p-4" role="status" aria-label="Loading run details">
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
          Select an optimization run from the table to inspect holdout scores, FP/FN diagnostics,
          instruction diffs, logs, and promotion controls.
        </p>
      </div>
    )
  }

  const report = asRecord(job.report_json)
  const isQualifying = job.saved !== false && report?.eligible !== false
  const canPromote =
    job.status === 'completed' &&
    Boolean(job.artifact_object_key) &&
    Boolean(job.artifact_sha256) &&
    activePromotionJobId !== job.id
  const isCurrentlyPromoted = activePromotionJobId === job.id

  const fpBefore = report?.fp_rate_before
  const fpAfter = report?.fp_rate_after
  const diagAfter = asRecord(report?.diagnostics_after)
  const fittedParams = asRecord(report?.fitted_parameters)
  const rlmMetrics = asRecord(report?.metrics)
  const baseInstr = asRecord(report?.baseline_instructions)
  const optInstr = asRecord(report?.optimized_instructions)
  const blockingFailures = Array.isArray(report?.blocking_failures)
    ? report.blocking_failures.length
    : null
  const pairedRegressions = Array.isArray(report?.paired_regressions)
    ? report.paired_regressions.length
    : null

  const delta =
    job.baseline_score !== null && job.optimized_score !== null
      ? (job.optimized_score - job.baseline_score) * 100
      : null

  const safeMlflowUrl =
    job.mlflow_run_url && /^https?:\/\//i.test(job.mlflow_run_url) ? job.mlflow_run_url : null

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4 rounded-xl border bg-card p-4" data-testid="run-inspector">
        <div className="flex flex-wrap items-start justify-between gap-2 border-b border-border/60 pb-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className={typeClass('subtitle')} style={{ fontVariationSettings: fontWeights.semibold }}>
                {job.target === 'skills'
                  ? 'Skill Selector'
                  : job.target === 'memory'
                    ? 'Memory Gate'
                    : 'Root RLM'}
              </h3>
              <Badge variant={job.status === 'completed' ? 'secondary' : job.status === 'failed' ? 'destructive' : 'outline'}>
                {job.status}
              </Badge>
              {isCurrentlyPromoted && <Badge variant="default">Active in runtime</Badge>}
            </div>
            <p className={`${typeClass('caption')} mt-1 text-muted-foreground`}>
              {job.dataset_name ?? job.dataset_source} · Started {formatTimestamp(job.started_at ?? job.created_at)}
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
                setPromoteOpen(true)
              }}
              data-testid="promote-run-button"
            >
              <Sparkles className="size-3.5" aria-hidden="true" />
              {isCurrentlyPromoted ? 'Promoted' : 'Promote'}
            </Button>
          </div>
        </div>

        {/* 2x2 KPI metrics */}
        <dl className="grid grid-cols-2 gap-3">
          <div className="rounded-lg border p-3">
            <dt className={`${typeClass('caption')} text-muted-foreground`}>Baseline holdout</dt>
            <dd className={`${typeClass('title')} tabular-nums text-foreground`} style={{ fontVariationSettings: fontWeights.semibold }}>
              {formatScore(job.baseline_score)}
            </dd>
          </div>
          <div className="rounded-lg border p-3">
            <dt className={`${typeClass('caption')} text-muted-foreground`}>Candidate holdout</dt>
            <dd className={`${typeClass('title')} tabular-nums text-foreground`} style={{ fontVariationSettings: fontWeights.semibold }}>
              {formatScore(job.optimized_score)}
              {delta !== null && (
                <span className={`${typeClass('caption')} ml-2 font-normal text-muted-foreground`}>
                  {delta >= 0 ? '+' : ''}
                  {delta.toFixed(1)} pts
                </span>
              )}
            </dd>
          </div>
          <div className="rounded-lg border p-3">
            <dt className={`${typeClass('caption')} text-muted-foreground`}>Dataset split</dt>
            <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
              {job.train_size ?? '—'} train
              {job.val_size ? ` · ${job.val_size} val` : ''} · {job.holdout_size ?? '—'} holdout
            </dd>
          </div>
          <div className="rounded-lg border p-3">
            <dt className={`${typeClass('caption')} text-muted-foreground`}>Gate status</dt>
            <dd className={`${typeClass('body')} flex items-center gap-1.5 text-foreground`}>
              <CheckCircle2 className="size-3.5 text-muted-foreground" aria-hidden="true" />
              {job.saved === true ? 'Saved & qualified' : job.saved === false ? 'Gate rejected candidate' : job.stage}
            </dd>
          </div>
        </dl>

        {job.error && (
          <Alert variant="destructive">
            <AlertDescription>{job.error}</AlertDescription>
          </Alert>
        )}

        {/* ReAnchor diagnostics */}
        {job.target !== 'rlm' && (fpBefore !== undefined || diagAfter) && (
          <div className="flex flex-col gap-2 rounded-lg border p-3">
            <h4 className={typeClass('body')} style={{ fontVariationSettings: fontWeights.semibold }}>
              Calibration diagnostics
            </h4>
            <dl className="grid grid-cols-2 gap-2">
              <div>
                <dt className={`${typeClass('caption')} text-muted-foreground`}>False-positive rate</dt>
                <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                  {formatRate(fpBefore)} → {formatRate(fpAfter)}
                </dd>
              </div>
              {diagAfter && typeof diagAfter.fn_rate === 'number' && (
                <div>
                  <dt className={`${typeClass('caption')} text-muted-foreground`}>False-negative rate</dt>
                  <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                    {formatRate(diagAfter.fn_rate)}
                  </dd>
                </div>
              )}
              {diagAfter && typeof diagAfter.primary_accuracy === 'number' && (
                <div>
                  <dt className={`${typeClass('caption')} text-muted-foreground`}>Primary skill accuracy</dt>
                  <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                    {formatRate(diagAfter.primary_accuracy)}
                  </dd>
                </div>
              )}
              {diagAfter && typeof diagAfter.action_accuracy === 'number' && (
                <div>
                  <dt className={`${typeClass('caption')} text-muted-foreground`}>Action / scope accuracy</dt>
                  <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                    {formatRate(diagAfter.action_accuracy)} · {formatRate(diagAfter.scope_accuracy)}
                  </dd>
                </div>
              )}
            </dl>
          </div>
        )}

        {/* RLM holdout execution metrics */}
        {job.target === 'rlm' && rlmMetrics && (
          <div className="flex flex-col gap-2 rounded-lg border p-3">
            <h4 className={typeClass('body')} style={{ fontVariationSettings: fontWeights.semibold }}>
              RLM holdout metrics
            </h4>
            <dl className="grid grid-cols-2 gap-2">
              <div>
                <dt className={`${typeClass('caption')} text-muted-foreground`}>Submit rate</dt>
                <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                  {formatRate(rlmMetrics.baseline_submit_rate)} → {formatRate(rlmMetrics.optimized_submit_rate)}
                </dd>
              </div>
              <div>
                <dt className={`${typeClass('caption')} text-muted-foreground`}>Blocking / regressions</dt>
                <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                  {blockingFailures ?? 0} blocking · {pairedRegressions ?? 0} regressions
                </dd>
              </div>
            </dl>
          </div>
        )}

        {/* ReAnchor fitted thresholds & weights */}
        {fittedParams && Object.keys(fittedParams).length > 0 && (
          <CodeBlock size="sm">
            <CodeBlockHeader>
              <CodeBlockTitle>Fitted ReAnchor thresholds &amp; weights</CodeBlockTitle>
              <CodeBlockActions>
                <CodeBlockCopyButton value={JSON.stringify(fittedParams, null, 2)} aria-label="Copy fitted parameters" />
              </CodeBlockActions>
            </CodeBlockHeader>
            <CodeBlockContent wrap className="max-h-48 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
              {JSON.stringify(fittedParams, null, 2)}
            </CodeBlockContent>
          </CodeBlock>
        )}

        {/* GEPA instruction comparison */}
        {baseInstr && optInstr && Object.keys(optInstr).length > 0 && (
          <div className="flex flex-col gap-2">
            {Object.entries(optInstr).map(([predName, afterText]) => {
              const beforeText = typeof baseInstr[predName] === 'string' ? baseInstr[predName] : ''
              const changed = beforeText !== String(afterText)
              return (
                <CodeBlock key={predName} size="sm">
                  <CodeBlockHeader>
                    <CodeBlockTitle>
                      {predName} instructions ({changed ? 'modified' : 'unchanged'})
                    </CodeBlockTitle>
                    <CodeBlockActions>
                      <CodeBlockCopyButton value={String(afterText)} aria-label={`Copy ${predName} instructions`} />
                    </CodeBlockActions>
                  </CodeBlockHeader>
                  <CodeBlockContent wrap className="max-h-44 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
                    {String(afterText)}
                  </CodeBlockContent>
                </CodeBlock>
              )
            })}
          </div>
        )}

        {/* Artifact provenance */}
        {job.artifact_sha256 && (
          <div className={`${typeClass('caption')} flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-muted-foreground`}>
            <span>Artifact SHA-256</span>
            <code className="font-mono text-foreground">{job.artifact_sha256.slice(0, 16)}…</code>
          </div>
        )}

        {job.log_tail && (
          <CodeBlock size="sm">
            <CodeBlockHeader>
              <CodeBlockTitle>Run output tail</CodeBlockTitle>
              <CodeBlockActions>
                <CodeBlockCopyButton value={job.log_tail} aria-label="Copy run logs" />
              </CodeBlockActions>
            </CodeBlockHeader>
            <CodeBlockContent wrap className="max-h-56 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
              {job.log_tail}
            </CodeBlockContent>
          </CodeBlock>
        )}
      </div>

      <AlertDialog open={promoteOpen} onOpenChange={setPromoteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Promote candidate artifact to live traffic?</AlertDialogTitle>
            <AlertDialogDescription>
              Stores the verified SHA-256 artifact as the active runtime override across all API
              workers immediately, without restarting the server.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="flex flex-col gap-3">
            {!isQualifying && (
              <Alert variant="destructive">
                <AlertDescription className="flex flex-col gap-2">
                  <span>
                    This candidate did not pass the automatic holdout promotion gate. Only promote if
                    you explicitly intend to override the quality gate.
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
                  label={`Pin decision_model to ${job.resolved_decision_model}`}
                  checked={pinDecisionModel}
                  onToggle={() => setPinDecisionModel((prev) => !prev)}
                />
                <p className={`${typeClass('caption')} mt-1 text-muted-foreground`}>
                  ReAnchor thresholds are calibrated for this exact Jev snapshot.
                </p>
              </div>
            )}
          </div>

          <AlertDialogFooter>
            <AlertDialogCancel disabled={promoting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={promoting || (!isQualifying && !confirmNonQualifying)}
              onClick={async () => {
                setPromoting(true)
                try {
                  await onPromote(job.id, {
                    confirm_non_qualifying: !isQualifying && confirmNonQualifying,
                    pin_decision_model: pinDecisionModel,
                  })
                  setPromoteOpen(false)
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
