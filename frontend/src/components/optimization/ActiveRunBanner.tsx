import { ChevronDown, Square, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
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
import {
  Banner,
  BannerAction,
  BannerActions,
  BannerDescription,
  BannerTitle,
} from '@/components/ui/banner'
import { Button } from '@/components/ui/button'
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockTitle,
} from '@/components/ui/code-block'
import { ThinkingIndicator } from '@/components/ui/thinking-indicator'
import {
  ThinkingStep,
  ThinkingSteps,
  ThinkingStepsContent,
  ThinkingStepsHeader,
} from '@/components/ui/thinking-steps'
import type { OptimizationJobDetail, OptimizationTarget } from '@/lib/api'
import { fontWeights } from '@/lib/font-weight'
import { spring } from '@/lib/springs'
import { typeClass } from '@/lib/type-scale'

const TARGET_LABEL: Record<OptimizationTarget, string> = {
  skills: 'Skill Selector',
  memory: 'Memory Gate',
  rlm: 'Root RLM',
}

interface StageDefinition {
  key: string
  label: string
}

function stagesForJob(job: OptimizationJobDetail): StageDefinition[] {
  if (job.target === 'rlm') {
    const base: StageDefinition[] = [{ key: 'loading_dataset', label: 'Load dataset' }]
    if (job.config_json?.branch_mode === 'auto_neon') {
      base.unshift({ key: 'provisioning_branch', label: 'Provision Neon branch' })
    }
    if (!job.config_json?.local_interpreter) {
      base.push({ key: 'provisioning_sandboxes', label: 'Provision Daytona sandboxes' })
    }
    base.push(
      { key: 'baseline_eval', label: 'Baseline holdout eval' },
      { key: 'gepa_compile', label: 'GEPA prompt evolution' },
      { key: 'holdout_eval', label: 'Candidate holdout eval' },
      { key: 'saving_artifact', label: 'Holdout quality gate' },
    )
    return base
  }
  const stages: StageDefinition[] = []
  if (job.config_json?.branch_mode === 'auto_neon') {
    stages.push({ key: 'provisioning_branch', label: 'Provision Neon branch' })
  }
  stages.push(
    { key: 'loading_dataset', label: 'Load dataset' },
    { key: 'reanchor_cv', label: 'ReAnchor 5-fold CV' },
    { key: 'holdout_eval', label: 'Holdout FP/FN eval' },
    { key: 'saving_artifact', label: 'Save quality gate' },
  )
  return stages
}

function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

export function ActiveRunBanner({
  job,
  onCancel,
  onInspect,
  onDismiss,
}: {
  job: OptimizationJobDetail | null
  onCancel: (jobId: string) => Promise<void>
  onInspect: (jobId: string) => void
  onDismiss: () => void
}) {
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [showLogs, setShowLogs] = useState(false)

  const isRunning = Boolean(job && (job.status === 'queued' || job.status === 'running'))
  const progress = job?.progress_json ?? {}
  const completed = typeof progress.completed === 'number' ? progress.completed : null
  const total = typeof progress.total === 'number' && progress.total > 0 ? progress.total : null
  const isDeterminate = completed !== null && total !== null && completed >= 0

  const bannerStatus = !job
    ? 'info'
    : job.status === 'completed'
      ? 'success'
      : job.status === 'failed'
        ? 'error'
        : job.status === 'cancelled'
          ? 'warning'
          : 'info'

  const stages = job ? stagesForJob(job) : []
  const currentStageKey = progress.stage || job?.stage || 'queued'
  const currentStageIdx = stages.findIndex((item) => item.key === currentStageKey)

  return (
    <>
      <AnimatePresence initial={false}>
        {job && (
          <motion.section
            key={job.id}
            aria-label="Active optimization run"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4, transition: spring.moderate.exit }}
            transition={spring.moderate}
            className="flex flex-col gap-3"
          >
            <Banner status={bannerStatus} contrast="low" className="ring-1 ring-foreground/10">
              <BannerTitle>
                {TARGET_LABEL[job.target]} · {job.dataset_name ?? job.dataset_source}
              </BannerTitle>
              <BannerDescription>
                <span className="flex flex-col gap-2">
                  <span className={`${typeClass('caption')} text-muted-foreground`}>
                    {progress.label ?? job.stage}
                    {progress.message ? ` — ${progress.message}` : ''}
                    {job.error ? ` — ${job.error}` : ''}
                  </span>
                  {isRunning && (
                    <span className="flex items-center gap-3">
                      <progress
                        className="opt-progress flex-1"
                        aria-label={`${TARGET_LABEL[job.target]} optimization progress`}
                        max={isDeterminate ? total : undefined}
                        value={isDeterminate ? Math.min(completed, total) : undefined}
                      />
                      <span className={`${typeClass('caption')} shrink-0 tabular-nums text-foreground`}>
                        {isDeterminate
                          ? `${completed}/${total}`
                          : completed !== null
                            ? `${completed} evals`
                            : 'Running'}
                      </span>
                    </span>
                  )}
                </span>
              </BannerDescription>
              <BannerActions>
                <BannerAction variant="ghost" onClick={() => onInspect(job.id)}>
                  Inspect
                </BannerAction>
                <BannerAction variant="secondary" onClick={() => setShowLogs((prev) => !prev)}>
                  <ChevronDown
                    className={`size-3.5 transition-transform duration-160 ${showLogs ? 'rotate-180' : ''}`}
                    aria-hidden="true"
                  />
                  {showLogs ? 'Hide logs' : 'Logs'}
                </BannerAction>
                {isRunning ? (
                  <BannerAction
                    variant="primary"
                    disabled={job.cancel_requested || cancelling}
                    onClick={() => setConfirmCancel(true)}
                  >
                    <Square className="size-3" aria-hidden="true" />
                    {job.cancel_requested ? 'Cancelling…' : 'Cancel'}
                  </BannerAction>
                ) : (
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    aria-label="Dismiss run banner"
                    onClick={onDismiss}
                  >
                    <X className="size-3.5" aria-hidden="true" />
                  </Button>
                )}
              </BannerActions>
            </Banner>

            <div className="grid grid-cols-1 gap-3 rounded-xl border bg-card p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
              <div className="flex min-w-0 flex-col gap-2">
                {isRunning && <ThinkingIndicator size="compact" className="px-1 py-0" />}
                <ThinkingSteps
                  running={isRunning}
                  defaultOpen={isRunning}
                  size="compact"
                  className="w-full"
                >
                  <ThinkingStepsHeader meta={`${stages.length} stages`}>
                    Execution stages
                  </ThinkingStepsHeader>
                  <ThinkingStepsContent>
                    {stages.map((item, idx) => {
                      let stepStatus: 'complete' | 'active' | 'upcoming' | 'error' = 'upcoming'
                      if (job.status === 'completed' || currentStageKey === 'completed') {
                      stepStatus = 'complete'
                    } else if (job.status === 'failed' || job.status === 'cancelled') {
                      if (currentStageIdx === -1) stepStatus = idx === 0 ? 'error' : 'upcoming'
                      else if (idx < currentStageIdx) stepStatus = 'complete'
                      else if (idx === currentStageIdx) stepStatus = 'error'
                      else stepStatus = 'upcoming'
                    } else if (currentStageIdx !== -1) {
                      if (idx < currentStageIdx) stepStatus = 'complete'
                      else if (idx === currentStageIdx) stepStatus = 'active'
                      else stepStatus = 'upcoming'
                    } else if (idx === 0) {
                      stepStatus = 'active'
                    }
                    return (
                      <ThinkingStep
                        key={item.key}
                        label={item.label}
                        status={stepStatus}
                        isLast={idx === stages.length - 1}
                        meta={
                          stepStatus === 'active' && completed !== null
                            ? total !== null
                              ? `${completed}/${total}`
                              : `${completed}`
                            : undefined
                        }
                      />
                    )
                    })}
                  </ThinkingStepsContent>
                </ThinkingSteps>
              </div>

              <dl className="grid grid-cols-2 gap-x-5 gap-y-1 sm:text-right">
                <div>
                  <dt className={`${typeClass('caption')} text-muted-foreground`}>Baseline</dt>
                  <dd
                    className={`${typeClass('subtitle')} tabular-nums text-foreground`}
                    style={{ fontVariationSettings: fontWeights.semibold }}
                  >
                    {formatMetric(job.baseline_score ?? progress.baseline_score)}
                  </dd>
                </div>
                <div>
                  <dt className={`${typeClass('caption')} text-muted-foreground`}>Candidate</dt>
                  <dd
                    className={`${typeClass('subtitle')} tabular-nums text-foreground`}
                    style={{ fontVariationSettings: fontWeights.semibold }}
                  >
                    {formatMetric(job.optimized_score ?? progress.best_score)}
                  </dd>
                </div>
              </dl>
            </div>

            {showLogs && (
              <CodeBlock size="sm">
                <CodeBlockHeader>
                  <CodeBlockTitle>Optimizer output</CodeBlockTitle>
                  <CodeBlockActions>
                    <CodeBlockCopyButton
                      value={job.log_tail || 'No optimizer logs yet.'}
                      aria-label="Copy optimizer logs"
                    />
                  </CodeBlockActions>
                </CodeBlockHeader>
                <CodeBlockContent
                  wrap
                  className="max-h-56 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
                >
                  {job.log_tail || 'Waiting for subprocess output…'}
                </CodeBlockContent>
              </CodeBlock>
            )}
          </motion.section>
        )}
      </AnimatePresence>

      <AlertDialog open={confirmCancel} onOpenChange={setConfirmCancel}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel running optimization?</AlertDialogTitle>
            <AlertDialogDescription>
              Sends SIGTERM to the isolated optimizer subprocess so any ephemeral Daytona sandboxes
              and Neon branches are torn down cleanly before stopping.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={cancelling}>Keep running</AlertDialogCancel>
            <AlertDialogAction
              disabled={cancelling}
              onClick={async () => {
                if (!job) return
                setCancelling(true)
                try {
                  await onCancel(job.id)
                  setConfirmCancel(false)
                } finally {
                  setCancelling(false)
                }
              }}
            >
              {cancelling ? 'Cancelling…' : 'Cancel run'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
