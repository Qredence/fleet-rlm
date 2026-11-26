import { ChevronDown, Square } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useMemo, useState } from 'react'
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
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockTitle,
} from '@/components/ui/code-block'
import {
  ThinkingStep,
  ThinkingSteps,
  ThinkingStepsContent,
  ThinkingStepsHeader,
} from '@/components/ui/thinking-steps'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
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

interface StageTimelineEntry {
  stage: string
  status?: string
  started_at?: string | null
  finished_at?: string | null
  duration_seconds?: number | null
  duration_s?: number | null
  duration_ms?: number | null
  elapsed_seconds?: number | null
}

function stagesForJob(job: OptimizationJobDetail): StageDefinition[] {
  if (job.target === 'rlm') {
    const base: StageDefinition[] = [{ key: 'loading_dataset', label: 'Load dataset' }]
    if (job.config_json?.branch_mode === 'auto_neon') {
      base.unshift({ key: 'provisioning_branch', label: 'Provision Neon branch' })
    }
    const programKind =
      job.program_kind ??
      (typeof job.config_json?.program_kind === 'string' ? job.config_json.program_kind : null)
    const isFlex = programKind === 'flex'
    const isLocal = job.config_json?.local_interpreter === true
    if (!isFlex && !isLocal) {
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

function parseStageTimeline(progress: Record<string, unknown>): StageTimelineEntry[] {
  const raw = progress.stage_timeline
  if (!Array.isArray(raw)) return []
  const entries: StageTimelineEntry[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const rec = item as Record<string, unknown>
    if (typeof rec.stage !== 'string' || !rec.stage) continue
    entries.push({
      stage: rec.stage,
      status: typeof rec.status === 'string' ? rec.status : undefined,
      started_at: typeof rec.started_at === 'string' ? rec.started_at : null,
      finished_at: typeof rec.finished_at === 'string' ? rec.finished_at : null,
      duration_seconds: typeof rec.duration_seconds === 'number' ? rec.duration_seconds : null,
      duration_s: typeof rec.duration_s === 'number' ? rec.duration_s : null,
      duration_ms: typeof rec.duration_ms === 'number' ? rec.duration_ms : null,
      elapsed_seconds: typeof rec.elapsed_seconds === 'number' ? rec.elapsed_seconds : null,
    })
  }
  return entries
}

function formatStageDuration(entry: StageTimelineEntry | undefined): string | null {
  if (!entry) return null
  let seconds: number | null = null
  if (typeof entry.duration_seconds === 'number' && Number.isFinite(entry.duration_seconds)) {
    seconds = entry.duration_seconds
  } else if (typeof entry.duration_s === 'number' && Number.isFinite(entry.duration_s)) {
    seconds = entry.duration_s
  } else if (typeof entry.elapsed_seconds === 'number' && Number.isFinite(entry.elapsed_seconds)) {
    seconds = entry.elapsed_seconds
  } else if (typeof entry.duration_ms === 'number' && Number.isFinite(entry.duration_ms)) {
    seconds = entry.duration_ms / 1000
  } else if (entry.started_at && entry.finished_at) {
    const startMs = Date.parse(entry.started_at)
    const endMs = Date.parse(entry.finished_at)
    if (Number.isFinite(startMs) && Number.isFinite(endMs) && endMs >= startMs) {
      seconds = (endMs - startMs) / 1000
    }
  }
  if (seconds === null || seconds < 0) return null
  if (seconds < 1) return `${Math.max(1, Math.round(seconds * 1000))}ms`
  if (seconds < 10) return `${seconds.toFixed(1)}s`
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const rem = Math.round(seconds % 60)
  return rem > 0 ? `${mins}m ${rem}s` : `${mins}m`
}

function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function resolveStageIndex(
  stages: StageDefinition[],
  currentStageKey: string,
  timeline: StageTimelineEntry[],
  progressRecord: Record<string, unknown>,
  trackedStageKey: string | undefined,
): number {
  const indexOfStage = (key: string | undefined | null): number => {
    if (!key) return -1
    const exact = stages.findIndex((item) => item.key === key)
    if (exact !== -1) return exact
    if (key === 'starting' || key === 'queued') return stages.length > 0 ? 0 : -1
    return -1
  }

  const direct = indexOfStage(currentStageKey)
  if (direct !== -1) return direct

  for (let i = timeline.length - 1; i >= 0; i -= 1) {
    const idx = indexOfStage(timeline[i]?.stage)
    if (idx !== -1) return idx
  }

  const lastFromProgress =
    typeof progressRecord.last_active_stage === 'string'
      ? indexOfStage(progressRecord.last_active_stage)
      : -1
  if (lastFromProgress !== -1) return lastFromProgress

  const tracked = indexOfStage(trackedStageKey)
  if (tracked !== -1) return tracked

  return -1
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
  const [lastStageByJob, setLastStageByJob] = useState<Record<string, string>>({})

  const isRunning = Boolean(job && (job.status === 'queued' || job.status === 'running'))
  const progress = job?.progress_json ?? {}
  const progressRecord = progress as Record<string, unknown>
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

  const stages = useMemo(() => (job ? stagesForJob(job) : []), [job])
  const timeline = useMemo(() => parseStageTimeline(progressRecord), [progressRecord])
  const timelineByStage = useMemo(() => {
    const map = new Map<string, StageTimelineEntry>()
    for (const entry of timeline) {
      map.set(entry.stage, entry)
    }
    return map
  }, [timeline])

  const currentStageKey = progress.stage || job?.stage || 'queued'
  const isKnownNonTerminalStage = useMemo(
    () =>
      stages.some((item) => item.key === currentStageKey) ||
      currentStageKey === 'starting' ||
      currentStageKey === 'queued',
    [currentStageKey, stages],
  )

  // Remember the last non-terminal stage per job during render so terminal transitions
  // (failed / cancelled) highlight the stage that actually failed even when stage_timeline is absent.
  const rememberedStage =
    job && isKnownNonTerminalStage
      ? currentStageKey === 'starting' || currentStageKey === 'queued'
        ? (stages[0]?.key ?? currentStageKey)
        : currentStageKey
      : null
  if (job && rememberedStage && lastStageByJob[job.id] !== rememberedStage) {
    setLastStageByJob((prev) => ({ ...prev, [job.id]: rememberedStage }))
  }

  const resolvedStageIdx = job
    ? resolveStageIndex(stages, currentStageKey, timeline, progressRecord, lastStageByJob[job.id])
    : -1

  const completedStagesCount =
    !job
      ? 0
      : job.status === 'completed' || currentStageKey === 'completed'
        ? stages.length
        : resolvedStageIdx > 0
          ? resolvedStageIdx
          : 0

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
            <Banner
              status={bannerStatus}
              contrast="low"
              open
              dismissible={!isRunning}
              onDismiss={onDismiss}
              dismissLabel="Dismiss run banner"
              className="ring-1 ring-foreground/10"
            >
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
              {/* Markup order matches BANNER_ACTION_ORDER under BannerDescription: primary, secondary, ghost */}
              <BannerActions>
                {isRunning && (
                  <BannerAction
                    variant="primary"
                    disabled={job.cancel_requested || cancelling}
                    onClick={() => setConfirmCancel(true)}
                  >
                    <Square className="size-3" aria-hidden="true" />
                    {job.cancel_requested ? 'Cancelling…' : 'Cancel'}
                  </BannerAction>
                )}
                <BannerAction variant="secondary" onClick={() => setShowLogs((prev) => !prev)}>
                  <ChevronDown
                    className={`size-3.5 transition-transform duration-160 ${showLogs ? 'rotate-180' : ''}`}
                    aria-hidden="true"
                  />
                  {showLogs ? 'Hide logs' : 'Logs'}
                </BannerAction>
                <BannerAction variant="ghost" onClick={() => onInspect(job.id)}>
                  Inspect
                </BannerAction>
              </BannerActions>
            </Banner>

            <div className="grid grid-cols-1 gap-3 rounded-xl border bg-card p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
              <div className="flex min-w-0 flex-col gap-2">
                <ThinkingSteps
                  running={isRunning}
                  defaultOpen={isRunning}
                  size="compact"
                  className="w-full"
                >
                  <ThinkingStepsHeader meta={`${completedStagesCount}/${stages.length} stages`}>
                    {isRunning ? 'Optimizing target' : 'Execution stages'}
                  </ThinkingStepsHeader>
                  <ThinkingStepsContent>
                    {stages.map((item, idx) => {
                      let stepStatus: 'complete' | 'active' | 'upcoming' | 'error' = 'upcoming'
                      if (job.status === 'completed' || currentStageKey === 'completed') {
                        stepStatus = 'complete'
                      } else if (job.status === 'failed' || job.status === 'cancelled') {
                        if (resolvedStageIdx === -1) {
                          stepStatus = 'upcoming'
                        } else if (idx < resolvedStageIdx) {
                          stepStatus = 'complete'
                        } else if (idx === resolvedStageIdx) {
                          stepStatus = 'error'
                        } else {
                          stepStatus = 'upcoming'
                        }
                      } else {
                        const activeIdx = resolvedStageIdx !== -1 ? resolvedStageIdx : 0
                        if (idx < activeIdx) stepStatus = 'complete'
                        else if (idx === activeIdx) stepStatus = 'active'
                        else stepStatus = 'upcoming'
                      }

                      const duration = formatStageDuration(timelineByStage.get(item.key))
                      let meta: string | undefined
                      if (stepStatus === 'active') {
                        const progressMeta =
                          completed !== null
                            ? total !== null
                              ? `${completed}/${total}`
                              : `${completed}`
                            : null
                        meta =
                          progressMeta && duration
                            ? `${progressMeta} · ${duration}`
                            : (progressMeta ?? duration ?? undefined)
                      } else if (stepStatus === 'error') {
                        const terminalText = job.status === 'cancelled' ? 'Cancelled' : 'Failed'
                        meta = duration ? `${duration} · ${terminalText}` : terminalText
                      } else if (stepStatus === 'complete' && duration) {
                        meta = duration
                      }

                      return (
                        <ThinkingStep
                          key={item.key}
                          label={item.label}
                          status={stepStatus}
                          isLast={idx === stages.length - 1}
                          meta={meta}
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
                    <Tooltip>
                      <TooltipTrigger render={<span className="inline-flex" />}>
                        <CodeBlockCopyButton
                          value={job.log_tail || 'No optimizer logs yet.'}
                          aria-label="Copy optimizer logs"
                        />
                      </TooltipTrigger>
                      <TooltipContent side="top">Copy optimizer logs</TooltipContent>
                    </Tooltip>
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
