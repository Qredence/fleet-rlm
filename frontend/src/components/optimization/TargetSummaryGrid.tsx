import { formatScore, holdoutPoints } from './holdout-score'
import { Brain, Code2, Package, Play, RotateCcw } from 'lucide-react'
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
import { Skeleton } from '@/components/ui/skeleton'
import {
  errorMessage,
  type OptimizationOverview,
  type OptimizationTarget,
  type TargetStatusSummary,
} from '@/lib/api'
import { Elevated } from '@/lib/elevated'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

const TARGET_META: Record<
  OptimizationTarget,
  { title: string; subtitle: string; plainGuide: string; icon: typeof Package }
> = {
  skills: {
    title: 'Skill Selector',
    subtitle: 'ReAnchor per-turn routing thresholds',
    plainGuide:
      'Runs before each chat turn to pick which workspace skills to load, avoiding noisy context without missing needed skills.',
    icon: Package,
  },
  memory: {
    title: 'Memory Gate',
    subtitle: 'ReAnchor save, action & scope gate',
    plainGuide:
      'Runs during turn consolidation to decide whether to save a memory and its scope, cutting false-positive saves.',
    icon: Brain,
  },
  rlm: {
    title: 'Root RLM',
    subtitle: 'GEPA instruction & sandbox budget',
    plainGuide:
      'Guides the sandboxed Python agent during chat turns; GEPA evolves prompt instructions with strict regression checks.',
    icon: Code2,
  },
}

const SOURCE_BADGE: Record<
  TargetStatusSummary['source'],
  { label: string; variant: 'default' | 'secondary' | 'outline' }
> = {
  promoted: { label: 'Promoted', variant: 'default' },
  env: { label: 'Env artifact', variant: 'secondary' },
  default: { label: 'Default baseline', variant: 'outline' },
}

function formatDelta(
  target: string,
  baseline: number | null | undefined,
  optimized: number | null | undefined,
): string | null {
  if (
    baseline === null ||
    baseline === undefined ||
    optimized === null ||
    optimized === undefined ||
    Number.isNaN(baseline) ||
    Number.isNaN(optimized)
  ) {
    return null
  }
  const delta = optimized - baseline
  const sign = delta >= 0 ? '+' : ''
  return `${sign}${holdoutPoints(delta, target)} pts`
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

export function TargetSummaryGrid({
  overview,
  loading,
  onLaunchTarget,
  onRollbackTarget,
}: {
  overview: OptimizationOverview | null
  loading: boolean
  onLaunchTarget: (target: OptimizationTarget) => void
  onRollbackTarget: (target: OptimizationTarget, mode: 'previous' | 'env') => Promise<void>
}) {
  const [rollbackState, setRollbackState] = useState<{
    summary: TargetStatusSummary
    mode: 'previous' | 'env'
    submitting: boolean
    error: string
  } | null>(null)

  if (!overview && loading) {
    return (
      <div className="opt-subgrid-cards" role="status" aria-label="Loading target status">
        {(['skills', 'memory', 'rlm'] as const).map((key) => (
          <Elevated
            key={key}
            offset={1}
            shadowLevel={2}
            className="opt-subgrid-card min-w-0 rounded-xl p-4 ring-1 ring-foreground/10"
          >
            <div className="flex items-center justify-between gap-2">
              <Skeleton className="h-5 w-32 bg-hover motion-reduce:animate-none" />
              <Skeleton className="h-5 w-20 bg-hover motion-reduce:animate-none" />
            </div>
            <Skeleton className="h-9 w-full bg-hover motion-reduce:animate-none" />
            <Skeleton className="h-12 w-full bg-hover motion-reduce:animate-none" />
            <div className="flex items-center justify-between gap-2 pt-1">
              <Skeleton className="h-8 w-24 bg-hover motion-reduce:animate-none" />
              <Skeleton className="h-8 w-20 bg-hover motion-reduce:animate-none" />
            </div>
          </Elevated>
        ))}
      </div>
    )
  }

  const targets = overview?.targets ?? []
  const previousPromo = rollbackState?.summary.previous_promotion ?? null

  return (
    <>
      <div className="opt-subgrid-cards" role="region" aria-label="Optimization targets">
        {targets.map((summary) => {
          const meta = TARGET_META[summary.target]
          const Icon = meta.icon
          const badge = SOURCE_BADGE[summary.source]
          const scored = summary.latest_scored_job ?? (
            summary.latest_job?.status === 'completed' && summary.latest_job.optimized_score != null
              ? summary.latest_job
              : null
          )
          const delta = formatDelta(
            summary.target,
            scored?.baseline_score,
            scored?.optimized_score,
          )
          const hasActive = Boolean(summary.active_job)
          const shaShort = summary.active_promotion?.artifact_sha256.slice(0, 12)
          const pinnedProvider = summary.active_promotion?.decision_provider_name
          const pinnedModel = summary.active_promotion?.resolved_decision_model

          return (
            <Elevated
              key={summary.target}
              offset={1}
              shadowLevel={2}
              data-testid={`target-card-${summary.target}`}
              className="opt-subgrid-card min-w-0 rounded-xl p-4 ring-1 ring-foreground/10"
            >
              {/* Subgrid Row 1: Title, plain-English role & active source badge */}
              <div className="flex flex-col gap-1.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-start gap-2.5">
                    <Icon
                      className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <h2
                        className={`${typeClass('subtitle')} truncate text-foreground`}
                        style={{ fontVariationSettings: fontWeights.semibold }}
                      >
                        {meta.title}
                      </h2>
                      <p className={`${typeClass('caption')} text-muted-foreground`}>
                        {meta.subtitle}
                      </p>
                    </div>
                  </div>
                  <Badge variant={badge.variant} className="shrink-0">
                    {badge.label}
                  </Badge>
                </div>
                <p className={`${typeClass('caption')} text-muted-foreground/90`}>
                  {meta.plainGuide}
                </p>
              </div>

              {/* Subgrid Row 2: Active artifact & pinned model provenance */}
              <div className="flex min-w-0 flex-col gap-1 border-t border-border/60 pt-2.5">
                {summary.active_promotion ? (
                  <>
                    <div className={`${typeClass('caption')} flex items-center justify-between gap-2`}>
                      <span className="text-muted-foreground">Artifact SHA</span>
                      <code className="truncate font-mono text-foreground">{shaShort}</code>
                    </div>
                    {pinnedModel && (
                      <div className={`${typeClass('caption')} flex items-center justify-between gap-2`}>
                        <span className="text-muted-foreground">Pinned model</span>
                        <span
                          className="truncate font-mono text-foreground"
                          title={pinnedProvider ? `${pinnedProvider} · ${pinnedModel}` : pinnedModel}
                        >
                          {pinnedProvider ? `${pinnedProvider} · ${pinnedModel}` : pinnedModel}
                        </span>
                      </div>
                    )}
                  </>
                ) : summary.env_path ? (
                  <div className={`${typeClass('caption')} flex items-center justify-between gap-2`}>
                    <span className="text-muted-foreground">Env path</span>
                    <code className="truncate font-mono text-foreground" title={summary.env_path}>
                      {summary.env_path}
                    </code>
                  </div>
                ) : (
                  <p className={`${typeClass('caption')} text-muted-foreground`}>
                    Serving built-in DSPy baseline
                  </p>
                )}
              </div>

              {/* Subgrid Row 3: Latest holdout performance */}
              <div className="flex min-w-0 items-baseline justify-between gap-2 border-t border-border/60 pt-2.5">
                {scored ? (
                  <>
                    <div className="flex flex-col">
                      <span className={`${typeClass('caption')} text-muted-foreground`}>
                        Latest holdout
                      </span>
                      <span
                        className={`${typeClass('title')} tabular-nums text-foreground`}
                        style={{ fontVariationSettings: fontWeights.semibold }}
                      >
                        {formatScore(scored.optimized_score, summary.target)}
                      </span>
                    </div>
                    <div className="text-right">
                      {delta && (
                        <div className={`${typeClass('caption')} tabular-nums text-foreground`}>
                          {delta}
                        </div>
                      )}
                      <div className={`${typeClass('caption')} tabular-nums text-muted-foreground`}>
                        base {formatScore(scored.baseline_score, summary.target)}
                      </div>
                    </div>
                  </>
                ) : (
                  <span className={`${typeClass('caption')} text-muted-foreground`}>
                    No scored optimization runs yet
                  </span>
                )}
              </div>

              {/* Subgrid Row 4: Action footer */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2.5">
                <Button
                  size="sm"
                  onClick={() => onLaunchTarget(summary.target)}
                  disabled={hasActive}
                  data-testid={`launch-target-${summary.target}`}
                >
                  <Play className="size-3.5" aria-hidden="true" />
                  {hasActive ? 'Running…' : 'Run'}
                </Button>

                {summary.can_rollback_env && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setRollbackState({
                        summary,
                        mode: summary.can_rollback_previous ? 'previous' : 'env',
                        submitting: false,
                        error: '',
                      })
                    }
                    data-testid={`rollback-target-${summary.target}`}
                  >
                    <RotateCcw className="size-3.5" aria-hidden="true" />
                    Rollback
                  </Button>
                )}
              </div>
            </Elevated>
          )
        })}
      </div>

      <AlertDialog
        open={Boolean(rollbackState)}
        onOpenChange={(open) => {
          if (!open && !rollbackState?.submitting) setRollbackState(null)
        }}
      >
        <AlertDialogContent className="sm:max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle>
              Roll back {rollbackState ? TARGET_META[rollbackState.summary.target].title : ''}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {rollbackState?.mode === 'previous'
                ? 'Reverts live traffic to the previously promoted artifact for this target immediately, without restarting backend workers.'
                : 'Clears the promoted runtime override and restores the deployment environment or default baseline artifact.'}
            </AlertDialogDescription>
          </AlertDialogHeader>

          {rollbackState && (
            <div className="flex flex-col gap-3">
              {rollbackState.summary.can_rollback_previous && (
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant={rollbackState.mode === 'previous' ? 'default' : 'outline'}
                    onClick={() =>
                      setRollbackState((prev) =>
                        prev ? { ...prev, mode: 'previous', error: '' } : prev,
                      )
                    }
                  >
                    Previous promotion
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={rollbackState.mode === 'env' ? 'default' : 'outline'}
                    onClick={() =>
                      setRollbackState((prev) =>
                        prev ? { ...prev, mode: 'env', error: '' } : prev,
                      )
                    }
                  >
                    Env / Default baseline
                  </Button>
                </div>
              )}

              {/* Exact restoration preview */}
              <div
                className="flex flex-col gap-2 rounded-lg border bg-muted/20 p-3"
                data-testid="rollback-preview"
              >
                <p
                  className={`${typeClass('caption')} text-foreground`}
                  style={{ fontVariationSettings: fontWeights.semibold }}
                >
                  Will restore:
                </p>
                {rollbackState.mode === 'previous' && previousPromo ? (
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
                    <div>
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>Artifact SHA</dt>
                      <dd className={`${typeClass('caption')} font-mono text-foreground`}>
                        {previousPromo.artifact_sha256.slice(0, 12)}
                      </dd>
                    </div>
                    <div>
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>Holdout score</dt>
                      <dd className={`${typeClass('caption')} font-mono tabular-nums text-foreground`}>
                        {formatScore(previousPromo.optimized_score, rollbackState.summary.target)}
                      </dd>
                    </div>
                    <div>
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>Pinned model</dt>
                      <dd className={`${typeClass('caption')} truncate font-mono text-foreground`}>
                        {previousPromo.decision_provider_name
                          ? `${previousPromo.decision_provider_name} · ${previousPromo.resolved_decision_model ?? 'default'}`
                          : (previousPromo.resolved_decision_model ?? 'Workspace default')}
                      </dd>
                    </div>
                    <div>
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>Promoted</dt>
                      <dd className={`${typeClass('caption')} text-foreground`}>
                        {formatTimestamp(previousPromo.promoted_at)}
                      </dd>
                    </div>
                  </dl>
                ) : rollbackState.mode === 'previous' ? (
                  <p className={`${typeClass('caption')} text-muted-foreground`}>
                    Previous promoted artifact for {TARGET_META[rollbackState.summary.target].title}.
                  </p>
                ) : rollbackState.summary.env_path ? (
                  <div className="flex flex-col gap-0.5">
                    <span className={`${typeClass('caption')} text-muted-foreground`}>
                      Deployment environment artifact
                    </span>
                    <code className={`${typeClass('caption')} break-all font-mono text-foreground`}>
                      {rollbackState.summary.env_path}
                    </code>
                  </div>
                ) : (
                  <p className={`${typeClass('caption')} text-foreground`}>
                    Built-in DSPy baseline (no artifact override).
                  </p>
                )}
              </div>

              {rollbackState.error && (
                <Alert variant="destructive">
                  <AlertDescription>{rollbackState.error}</AlertDescription>
                </Alert>
              )}
            </div>
          )}

          <AlertDialogFooter>
            <AlertDialogCancel disabled={rollbackState?.submitting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={rollbackState?.submitting}
              onClick={async () => {
                if (!rollbackState) return
                const { summary, mode } = rollbackState
                setRollbackState((prev) => (prev ? { ...prev, submitting: true, error: '' } : prev))
                try {
                  await onRollbackTarget(summary.target, mode)
                  setRollbackState(null)
                } catch (failure) {
                  setRollbackState((prev) =>
                    prev ? { ...prev, submitting: false, error: errorMessage(failure) } : null,
                  )
                }
              }}
            >
              {rollbackState?.submitting ? 'Rolling back…' : 'Confirm rollback'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
