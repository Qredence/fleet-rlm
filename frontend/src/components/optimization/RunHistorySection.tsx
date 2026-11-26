import { useState } from 'react'
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
import type { StatusFilter, TargetFilter } from '@/hooks/use-optimizations'
import { useMediaQuery } from '@/hooks/use-media-query'
import type {
  OptimizationJobDetail,
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
  onLaunchFirst: () => void
}) {
  const isWide = useMediaQuery('(min-width: 80rem)')
  const [drawerOpen, setDrawerOpen] = useState(false)

  const selectedIndex =
    jobs && selectedJobId ? jobs.findIndex((item) => item.id === selectedJobId) : -1
  const activePromotionJobId =
    overview?.targets.find((item) => item.target === selectedJob?.target)?.active_promotion?.job_id ?? null

  const handleRowSelect = (jobId: string) => {
    onSelectJob(jobId)
    if (!isWide) setDrawerOpen(true)
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
                <SelectItem index={0} value="all">All targets</SelectItem>
                <SelectItem index={1} value="skills">Skill Selector</SelectItem>
                <SelectItem index={2} value="memory">Memory Gate</SelectItem>
                <SelectItem index={3} value="rlm">Root RLM</SelectItem>
              </SelectContent>
            </Select>

            <Select
              size="compact"
              value={statusFilter}
              onValueChange={(value) => onStatusFilterChange((value || 'all') as StatusFilter)}
            >
              <SelectTrigger aria-label="Filter runs by status" placeholder="All statuses" />
              <SelectContent>
                <SelectItem index={0} value="all">All statuses</SelectItem>
                <SelectItem index={1} value="running">Running</SelectItem>
                <SelectItem index={2} value="completed">Completed</SelectItem>
                <SelectItem index={3} value="failed">Failed</SelectItem>
                <SelectItem index={4} value="cancelled">Cancelled</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="opt-runs-caption"
          className="opt-table-wrapper rounded-xl border bg-card focus-visible:outline-2 focus-visible:outline-ring"
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
                    const isPromoted = overview?.targets.some(
                      (item) => item.active_promotion?.job_id === job.id,
                    )
                    return (
                      <TableRow
                        key={job.id}
                        index={idx}
                        tabIndex={0}
                        onClick={() => handleRowSelect(job.id)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            handleRowSelect(job.id)
                          }
                        }}
                        className="cursor-pointer outline-none"
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
                          <span className="inline-flex items-center gap-1.5">
                            <Badge variant="ghost">{job.status}</Badge>
                            {isPromoted && <Badge variant="ghost">Active</Badge>}
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
        <div className="xl:sticky xl:top-4 xl:self-start">
          <RunInspectorPanel
            job={selectedJob}
            loading={loadingSelected}
            activePromotionJobId={activePromotionJobId}
            onPromote={onPromote}
            onRerun={onRerun}
          />
        </div>
      ) : (
        <Sheet open={drawerOpen && Boolean(selectedJobId)} onOpenChange={setDrawerOpen}>
          <SheetContent side="right" className="w-full overflow-y-auto data-[side=right]:sm:max-w-xl">
            <SheetHeader>
              <SheetTitle>Optimization run details</SheetTitle>
              <SheetDescription>
                Inspect holdout diagnostics, fitted weights, and promotion status.
              </SheetDescription>
            </SheetHeader>
            <div className="px-4 pb-6">
              <RunInspectorPanel
                job={selectedJob}
                loading={loadingSelected}
                activePromotionJobId={activePromotionJobId}
                onPromote={onPromote}
                onRerun={(job) => {
                  setDrawerOpen(false)
                  onRerun(job)
                }}
              />
            </div>
          </SheetContent>
        </Sheet>
      )}
    </div>
  )
}
