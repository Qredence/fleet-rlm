import { useRef, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useMediaQuery } from '@/hooks/use-media-query'
import type { StatusFilter, TargetFilter } from '@/hooks/use-optimizations'
import type {
  OptimizationJobDetail,
  OptimizationJobStatus,
  OptimizationJobSummary,
  OptimizationOverview,
} from '@/lib/api'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'
import { RunInspectorPanel } from './RunInspectorPanel'

const TARGET_LABEL = {
  skills: 'Skill Selector',
  memory: 'Memory Gate',
  rlm: 'Root RLM',
} as const

const STATUS_BADGE: Record<
  OptimizationJobStatus,
  { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' }
> = {
  queued: { label: 'Queued', variant: 'outline' },
  running: { label: 'Running', variant: 'default' },
  completed: { label: 'Completed', variant: 'secondary' },
  failed: { label: 'Failed', variant: 'destructive' },
  cancelled: { label: 'Cancelled', variant: 'outline' },
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function RunHistorySection({
  overview,
  jobs,
  loading,
  selectedJobId,
  selectedJob,
  loadingSelected,
  targetFilter,
  statusFilter,
  onTargetFilterChange,
  onStatusFilterChange,
  onSelectJob,
  onPromote,
  onRerun,
  onOpenFullDiff,
  onLaunchFirst,
}: {
  overview: OptimizationOverview | null
  jobs: OptimizationJobSummary[] | null
  loading: boolean
  selectedJobId: string | null
  selectedJob: OptimizationJobDetail | null
  loadingSelected: boolean
  targetFilter: TargetFilter
  statusFilter: StatusFilter
  onTargetFilterChange: (value: TargetFilter) => void
  onStatusFilterChange: (value: StatusFilter) => void
  onSelectJob: (jobId: string) => void
  onPromote: (
    jobId: string,
    opts: { confirm_non_qualifying: boolean; pin_decision_model: boolean },
  ) => Promise<void>
  onRerun: (job: OptimizationJobSummary) => void
  onOpenFullDiff?: (job: OptimizationJobDetail) => void
  onLaunchFirst: () => void
}) {
  const isWide = useMediaQuery('(min-width: 80rem)')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map())

  const selectedIndex =
    jobs && selectedJobId ? jobs.findIndex((item) => item.id === selectedJobId) : -1
  const activeTargetSummary =
    overview?.targets.find((item) => item.target === selectedJob?.target) ?? null
  const activePromotionJobId = activeTargetSummary?.active_promotion?.job_id ?? null

  const handleRowSelect = (jobId: string, openDrawerOnNarrow = true) => {
    onSelectJob(jobId)
    if (!isWide && openDrawerOnNarrow) setDrawerOpen(true)
  }

  const moveFocusByOffset = (currentIndex: number, offset: number) => {
    if (!jobs || jobs.length === 0) return
    const nextIndex = Math.max(0, Math.min(jobs.length - 1, currentIndex + offset))
    const nextJob = jobs[nextIndex]
    if (!nextJob) return
    onSelectJob(nextJob.id)
    rowRefs.current.get(nextJob.id)?.focus()
  }

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,7fr)_minmax(22rem,5fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className={typeClass('title')} style={{ fontVariationSettings: fontWeights.semibold }}>
            Optimization runs
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              size="compact"
              value={targetFilter}
              onValueChange={(value) => onTargetFilterChange((value || 'all') as TargetFilter)}
            >
              <SelectTrigger aria-label="Filter runs by target" placeholder="All targets" />
              <SelectContent>
                <SelectItem index={0} value="all">
                  All targets
                </SelectItem>
                <SelectItem index={1} value="skills">
                  Skill Selector
                </SelectItem>
                <SelectItem index={2} value="memory">
                  Memory Gate
                </SelectItem>
                <SelectItem index={3} value="rlm">
                  Root RLM
                </SelectItem>
              </SelectContent>
            </Select>

            <Select
              size="compact"
              value={statusFilter}
              onValueChange={(value) => onStatusFilterChange((value || 'all') as StatusFilter)}
            >
              <SelectTrigger aria-label="Filter runs by status" placeholder="All statuses" />
              <SelectContent>
                <SelectItem index={0} value="all">
                  All statuses
                </SelectItem>
                <SelectItem index={1} value="queued">
                  Queued
                </SelectItem>
                <SelectItem index={2} value="running">
                  Running
                </SelectItem>
                <SelectItem index={3} value="completed">
                  Completed
                </SelectItem>
                <SelectItem index={4} value="failed">
                  Failed
                </SelectItem>
                <SelectItem index={5} value="cancelled">
                  Cancelled
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="opt-runs-caption"
          className="opt-table-wrapper rounded-xl border bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {loading && !jobs ? (
            <div className="flex flex-col gap-2 p-4" role="status" aria-label="Loading optimization runs">
              {[0, 1, 2, 3].map((row) => (
                <Skeleton key={row} className="h-9 w-full bg-hover motion-reduce:animate-none" />
              ))}
            </div>
          ) : (
            <Table
              selectedIndices={selectedIndex >= 0 ? [selectedIndex] : []}
              data-testid="optimization-runs-table"
            >
              <caption id="opt-runs-caption" className="sr-only">
                Optimization run history
              </caption>
              <TableHeader>
                <TableRow>
                  <TableHead>Target</TableHead>
                  <TableHead>Dataset</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                  <TableHead className="text-right">Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {!jobs || jobs.length === 0 ? (
                  <TableEmpty
                    columns={5}
                    title="No optimization runs yet"
                    description="Launch a ReAnchor or GEPA run to measure holdout improvements."
                    action={
                      <button
                        type="button"
                        onClick={onLaunchFirst}
                        className={`${typeClass('body')} cursor-pointer text-foreground underline underline-offset-4`}
                      >
                        Start first run
                      </button>
                    }
                  />
                ) : (
                  jobs.map((job, idx) => {
                    const isSelected = job.id === selectedJobId
                    const isPromoted = overview?.targets.some(
                      (item) => item.active_promotion?.job_id === job.id,
                    )
                    const statusBadge = STATUS_BADGE[job.status]
                    return (
                      <TableRow
                        key={job.id}
                        ref={(node) => {
                          if (node) rowRefs.current.set(job.id, node)
                          else rowRefs.current.delete(job.id)
                        }}
                        index={idx}
                        tabIndex={0}
                        aria-selected={isSelected}
                        onClick={() => handleRowSelect(job.id)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            handleRowSelect(job.id)
                          } else if (event.key === 'ArrowDown') {
                            event.preventDefault()
                            moveFocusByOffset(idx, 1)
                          } else if (event.key === 'ArrowUp') {
                            event.preventDefault()
                            moveFocusByOffset(idx, -1)
                          } else if (event.key === 'Home') {
                            event.preventDefault()
                            moveFocusByOffset(0, 0)
                          } else if (event.key === 'End' && jobs.length > 0) {
                            event.preventDefault()
                            moveFocusByOffset(jobs.length - 1, 0)
                          }
                        }}
                        className="cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                        data-testid={`run-row-${job.id}`}
                      >
                        <TableCell data-label="Target">
                          <span style={{ fontVariationSettings: fontWeights.semibold }}>
                            {TARGET_LABEL[job.target]}
                          </span>
                        </TableCell>
                        <TableCell data-label="Dataset" className="max-w-44 truncate">
                          {job.dataset_name ?? job.dataset_source}
                        </TableCell>
                        <TableCell data-label="Status">
                          <span className="inline-flex flex-wrap items-center gap-1.5">
                            <Badge variant={statusBadge.variant}>{statusBadge.label}</Badge>
                            {isPromoted && <Badge variant="default">Active</Badge>}
                          </span>
                        </TableCell>
                        <TableCell data-label="Score" className="text-right tabular-nums">
                          {job.optimized_score !== null ? (
                            <span>
                              {formatScore(job.baseline_score)} → {formatScore(job.optimized_score)}
                            </span>
                          ) : (
                            job.stage
                          )}
                        </TableCell>
                        <TableCell data-label="Started" className="text-right tabular-nums">
                          {formatTime(job.started_at ?? job.created_at)}
                        </TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          )}
        </div>
      </div>

      {/* Desktop inline sticky inspector */}
      {isWide ? (
        <div className="min-w-0 xl:sticky xl:top-4 xl:self-start">
          <RunInspectorPanel
            job={selectedJob}
            loading={loadingSelected}
            activePromotionJobId={activePromotionJobId}
            activeTargetSummary={activeTargetSummary}
            onPromote={onPromote}
            onRerun={onRerun}
            onOpenFullDiff={onOpenFullDiff}
          />
        </div>
      ) : (
        <Sheet open={drawerOpen && Boolean(selectedJobId)} onOpenChange={setDrawerOpen}>
          <SheetContent side="right" className="w-full overflow-y-auto data-[side=right]:sm:max-w-xl">
            <SheetHeader className="pr-12">
              <SheetTitle>Optimization run details</SheetTitle>
              <SheetDescription>
                Inspect holdout diagnostics, gate checks, fitted parameters, and promotion controls.
              </SheetDescription>
            </SheetHeader>
            <div className="px-4 pb-6">
              <RunInspectorPanel
                job={selectedJob}
                loading={loadingSelected}
                activePromotionJobId={activePromotionJobId}
                activeTargetSummary={activeTargetSummary}
                onPromote={onPromote}
                onRerun={(job) => {
                  setDrawerOpen(false)
                  onRerun(job)
                }}
                onOpenFullDiff={
                  onOpenFullDiff
                    ? (job) => {
                        setDrawerOpen(false)
                        onOpenFullDiff(job)
                      }
                    : undefined
                }
              />
            </div>
          </SheetContent>
        </Sheet>
      )}
    </div>
  )
}
