import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  errorMessage,
  type FleetApi,
  type OptimizationDatasetSummary,
  type OptimizationJobDetail,
  type OptimizationJobStatus,
  type OptimizationJobSummary,
  type OptimizationOverview,
  type OptimizationTarget,
} from '@/lib/api'

export type TargetFilter = OptimizationTarget | 'all'
export type StatusFilter = OptimizationJobStatus | 'all'

export const SSE_MAX_RETRIES = 3
export const POLL_FALLBACK_INTERVAL_MS = 5_000
export const SSE_POLL_FALLBACK_WARNING =
  'Live progress stream disconnected after retries. Falling back to 5s polling until the run finishes.'

const ACTIVE_STATUSES = new Set<OptimizationJobStatus>(['queued', 'running'])
const TERMINAL_STATUSES = new Set<OptimizationJobStatus>(['completed', 'failed', 'cancelled'])
const NON_RETRYABLE_HTTP_STATUSES = new Set([401, 403, 404])

export function isActiveOptimizationStatus(status: OptimizationJobStatus): boolean {
  return ACTIVE_STATUSES.has(status)
}

export function isTerminalOptimizationStatus(status: OptimizationJobStatus): boolean {
  return TERMINAL_STATUSES.has(status)
}

function toJobDetail(summary: OptimizationJobSummary): OptimizationJobDetail {
  if ('report_json' in summary && 'log_tail' in summary) {
    return summary as OptimizationJobDetail
  }
  return {
    ...summary,
    report_json: null,
    log_tail: '',
  }
}

export function matchesOptimizationFilters(
  job: OptimizationJobSummary,
  targetFilter: TargetFilter,
  statusFilter: StatusFilter,
): boolean {
  if (targetFilter !== 'all' && job.target !== targetFilter) return false
  if (statusFilter !== 'all' && job.status !== statusFilter) return false
  return true
}

export function upsertJobForFilters(
  list: OptimizationJobSummary[] | null,
  updated: OptimizationJobSummary,
  targetFilter: TargetFilter,
  statusFilter: StatusFilter,
): OptimizationJobSummary[] {
  const existing = list ?? []
  const index = existing.findIndex((item) => item.id === updated.id)
  if (index !== -1) {
    const next = existing.slice()
    next[index] = updated
    return next
  }
  if (!matchesOptimizationFilters(updated, targetFilter, statusFilter)) {
    return existing
  }
  return [updated, ...existing]
}

export function pickSelectedJobId(
  current: string | null,
  nextJobs: OptimizationJobSummary[],
  nextOverview: OptimizationOverview | null,
  targetFilter: TargetFilter,
  statusFilter: StatusFilter,
): string | null {
  if (current && nextJobs.some((item) => item.id === current)) return current
  if (targetFilter === 'all' && statusFilter === 'all') {
    const active = nextOverview?.targets.find((item) => item.active_job)?.active_job
    if (active) return active.id
  }
  return nextJobs[0]?.id ?? null
}

export function collectActiveJobs(
  overview: OptimizationOverview | null,
  jobs: OptimizationJobSummary[] | null,
  liveJobsById: Record<string, OptimizationJobDetail>,
): OptimizationJobDetail[] {
  const byId = new Map<string, OptimizationJobDetail>()
  for (const target of overview?.targets ?? []) {
    if (target.active_job && isActiveOptimizationStatus(target.active_job.status)) {
      byId.set(target.active_job.id, toJobDetail(target.active_job))
    }
  }
  for (const item of jobs ?? []) {
    if (isActiveOptimizationStatus(item.status) && !byId.has(item.id)) {
      byId.set(item.id, toJobDetail(item))
    }
  }
  for (const detail of Object.values(liveJobsById)) {
    if (isActiveOptimizationStatus(detail.status)) {
      byId.set(detail.id, detail)
    } else {
      byId.delete(detail.id)
    }
  }
  return Array.from(byId.values()).sort((a, b) => b.created_at.localeCompare(a.created_at))
}

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve()
  return new Promise((resolve) => {
    const onAbort = () => {
      clearTimeout(timer)
      resolve()
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

export async function consumeSseStream(
  response: Response,
  signal: AbortSignal,
  onEvent: (eventName: string, data: OptimizationJobDetail) => void,
) {
  if (!response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = 'message'
  let dataLines: string[] = []

  const dispatch = () => {
    if (!dataLines.length) {
      eventName = 'message'
      return
    }
    const raw = dataLines.join('\n')
    const currentEvent = eventName
    eventName = 'message'
    dataLines = []
    try {
      const parsed = JSON.parse(raw) as OptimizationJobDetail
      if (parsed && typeof parsed === 'object' && 'id' in parsed) {
        onEvent(currentEvent, parsed)
      }
    } catch {
      // Ignore malformed keepalive or partial frames.
    }
  }

  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (line === '') {
          dispatch()
        } else if (line.startsWith(':')) {
          continue
        } else if (line.startsWith('event:')) {
          eventName = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart())
        }
      }
    }
    if (buffer.trim()) {
      const remainder = buffer.trim()
      if (remainder.startsWith('data:')) dataLines.push(remainder.slice(5).trimStart())
      dispatch()
    }
  } finally {
    await reader.cancel().catch(() => {})
  }
}

export interface StreamJobLifecycleOptions {
  api: Pick<FleetApi, 'openOptimizationEvents' | 'getOptimizationJob'>
  jobId: string
  signal: AbortSignal
  maxRetries?: number
  baseDelayMs?: number
  pollIntervalMs?: number
  onConnected?: () => void
  onUpdate: (detail: OptimizationJobDetail) => void
  onTerminal: (detail: OptimizationJobDetail) => void
  onFallbackWarning: (warning: string) => void
  onFatalError: (message: string) => void
}

export async function runOptimizationEventStream({
  api,
  jobId,
  signal,
  maxRetries = SSE_MAX_RETRIES,
  baseDelayMs = 1000,
  pollIntervalMs = POLL_FALLBACK_INTERVAL_MS,
  onConnected,
  onUpdate,
  onTerminal,
  onFallbackWarning,
  onFatalError,
}: StreamJobLifecycleOptions): Promise<'terminal' | 'aborted' | 'fatal'> {
  let retries = 0
  let terminal = false

  while (!signal.aborted && !terminal && retries <= maxRetries) {
    try {
      const response = await api.openOptimizationEvents(jobId, signal)
      onConnected?.()

      await consumeSseStream(response, signal, (eventName, detail) => {
        if (terminal || signal.aborted) return
        retries = 0
        onUpdate(detail)
        if (eventName === 'done' || isTerminalOptimizationStatus(detail.status)) {
          terminal = true
          onTerminal(detail)
        }
      })

      if (terminal || signal.aborted) break
    } catch (failure) {
      if (signal.aborted) break
      if (failure instanceof ApiError && NON_RETRYABLE_HTTP_STATUSES.has(failure.status)) {
        onFatalError(errorMessage(failure))
        return 'fatal'
      }
    }

    retries += 1
    if (retries > maxRetries) break
    const delayMs = baseDelayMs * 2 ** (retries - 1)
    await abortableSleep(delayMs, signal)
  }

  if (terminal) return 'terminal'
  if (signal.aborted) return 'aborted'

  onFallbackWarning(SSE_POLL_FALLBACK_WARNING)

  while (!signal.aborted && !terminal) {
    await abortableSleep(pollIntervalMs, signal)
    if (signal.aborted) break
    try {
      const detail = await api.getOptimizationJob(jobId, signal)
      if (signal.aborted) break
      onUpdate(detail)
      if (isTerminalOptimizationStatus(detail.status)) {
        terminal = true
        onTerminal(detail)
        break
      }
    } catch (failure) {
      if (signal.aborted) break
      if (failure instanceof ApiError && NON_RETRYABLE_HTTP_STATUSES.has(failure.status)) {
        onFatalError(errorMessage(failure))
        return 'fatal'
      }
    }
  }

  return terminal ? 'terminal' : 'aborted'
}

export function terminalAnnouncement(detail: OptimizationJobDetail): string {
  if (detail.status === 'completed') return `Optimization ${detail.target} completed.`
  if (detail.status === 'cancelled') return `Optimization ${detail.target} cancelled.`
  return `Optimization ${detail.target} failed.`
}

function fetchOptimizationSnapshot(
  api: FleetApi,
  targetFilter: TargetFilter,
  statusFilter: StatusFilter,
  signal: AbortSignal,
) {
  return Promise.all([
    api.getOptimizationOverview(signal),
    api.listOptimizationDatasets(signal),
    api.listOptimizationJobs(
      {
        target: targetFilter === 'all' ? undefined : targetFilter,
        status: statusFilter === 'all' ? undefined : statusFilter,
        limit: 50,
      },
      signal,
    ),
  ])
}

export function useOptimizations(api: FleetApi) {
  const [overview, setOverview] = useState<OptimizationOverview | null>(null)
  const [datasets, setDatasets] = useState<OptimizationDatasetSummary[] | null>(null)
  const [jobs, setJobs] = useState<OptimizationJobSummary[] | null>(null)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [fetchedSelectedJob, setFetchedSelectedJob] = useState<OptimizationJobDetail | null>(null)
  const [selectedFetchFailedId, setSelectedFetchFailedId] = useState<string | null>(null)
  const [liveJobsById, setLiveJobsById] = useState<Record<string, OptimizationJobDetail>>({})
  const [targetFilter, setTargetFilter] = useState<TargetFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [streamWarning, setStreamWarning] = useState('')
  const [announcement, setAnnouncement] = useState('')

  const refreshControllerRef = useRef<AbortController | null>(null)
  const jobsControllerRef = useRef<AbortController | null>(null)
  const selectedControllerRef = useRef<AbortController | null>(null)
  const streamControllersRef = useRef<Map<string, AbortController>>(new Map())
  const fatalStreamIdsRef = useRef<Set<string>>(new Set())

  const filtersRef = useRef({ targetFilter, statusFilter })
  const lastFetchedFilterKeyRef = useRef<string | null>(null)

  useEffect(() => {
    filtersRef.current = { targetFilter, statusFilter }
  }, [statusFilter, targetFilter])

  const applyJobUpdate = useCallback((detail: OptimizationJobDetail) => {
    setLiveJobsById((current) => ({ ...current, [detail.id]: detail }))
    setJobs((current) =>
      upsertJobForFilters(
        current,
        detail,
        filtersRef.current.targetFilter,
        filtersRef.current.statusFilter,
      ),
    )
    setFetchedSelectedJob((current) => (current?.id === detail.id ? detail : current))
  }, [])

  const applyJobs = useCallback(
    (
      controller: AbortController,
      nextJobs: OptimizationJobSummary[],
      target: TargetFilter,
      status: StatusFilter,
      nextOverview: OptimizationOverview | null = null,
    ) => {
      if (
        controller.signal.aborted ||
        jobsControllerRef.current !== controller ||
        filtersRef.current.targetFilter !== target ||
        filtersRef.current.statusFilter !== status
      ) return
      setError('')
      setJobs(nextJobs)
      setSelectedJobId((current) =>
        pickSelectedJobId(current, nextJobs, nextOverview, target, status),
      )
    },
    [],
  )

  const loadSnapshot = useCallback(
    async () => {
      refreshControllerRef.current?.abort()
      jobsControllerRef.current?.abort()
      const controller = new AbortController()
      refreshControllerRef.current = controller
      jobsControllerRef.current = controller
      fatalStreamIdsRef.current.clear()

      const { targetFilter: currentTarget, statusFilter: currentStatus } = filtersRef.current
      lastFetchedFilterKeyRef.current = `${currentTarget}:${currentStatus}`

      try {
        const [nextOverview, nextDatasets, nextJobs] = await fetchOptimizationSnapshot(
          api,
          currentTarget,
          currentStatus,
          controller.signal,
        )
        if (controller.signal.aborted) return
        setOverview(nextOverview)
        setDatasets(nextDatasets)
        applyJobs(controller, nextJobs, currentTarget, currentStatus, nextOverview)
      } catch (failure) {
        if (!controller.signal.aborted) setError(errorMessage(failure))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    },
    [api, applyJobs],
  )

  const refreshAll = useCallback(
    (opts?: { silent?: boolean }) => {
      if (!opts?.silent) setLoading(true)
      return loadSnapshot()
    },
    [loadSnapshot],
  )

  // Initial load (runs once per api instance; never re-runs on table filter changes).
  useEffect(() => {
    void loadSnapshot()
    return () => {
      refreshControllerRef.current?.abort()
      jobsControllerRef.current?.abort()
    }
  }, [loadSnapshot])

  // Filter changes only refetch the jobs list — never overview, datasets, or SSE.
  useEffect(() => {
    const filterKey = `${targetFilter}:${statusFilter}`
    if (lastFetchedFilterKeyRef.current === null || lastFetchedFilterKeyRef.current === filterKey) {
      return
    }
    lastFetchedFilterKeyRef.current = filterKey

    const previousRequest = jobsControllerRef.current
    if (previousRequest !== refreshControllerRef.current) previousRequest?.abort()
    const controller = new AbortController()
    jobsControllerRef.current = controller

    void api
      .listOptimizationJobs(
        {
          target: targetFilter === 'all' ? undefined : targetFilter,
          status: statusFilter === 'all' ? undefined : statusFilter,
          limit: 50,
        },
        controller.signal,
      )
      .then((nextJobs) => {
        applyJobs(controller, nextJobs, targetFilter, statusFilter)
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure))
      })

    return () => controller.abort()
  }, [api, applyJobs, statusFilter, targetFilter])

  // Derive selectedJob and loadingSelected synchronously from selectedJobId + liveJobsById + fetchedSelectedJob.
  const liveSelected = selectedJobId ? (liveJobsById[selectedJobId] ?? null) : null
  const fetchedMatch =
    selectedJobId && fetchedSelectedJob?.id === selectedJobId ? fetchedSelectedJob : null

  const selectedJob: OptimizationJobDetail | null = !selectedJobId
    ? null
    : (liveSelected ?? fetchedMatch)

  const needsSelectedFetch = Boolean(
    selectedJobId &&
      (!liveSelected ||
        (!isActiveOptimizationStatus(liveSelected.status) && liveSelected.report_json === null)) &&
      !fetchedMatch,
  )

  const loadingSelected = Boolean(
    selectedJobId &&
      !selectedJob &&
      needsSelectedFetch &&
      selectedFetchFailedId !== selectedJobId,
  )

  // Fetch full job detail only when selectedJobId needs detail from the backend.
  useEffect(() => {
    selectedControllerRef.current?.abort()
    if (!selectedJobId || !needsSelectedFetch) return

    const controller = new AbortController()
    selectedControllerRef.current = controller

    void api
      .getOptimizationJob(selectedJobId, controller.signal)
      .then((detail) => {
        if (controller.signal.aborted) return
        setSelectedFetchFailedId((current) => (current === selectedJobId ? null : current))
        setFetchedSelectedJob(detail)
        if (isActiveOptimizationStatus(detail.status)) {
          setLiveJobsById((current) => ({ ...current, [detail.id]: detail }))
        } else {
          setLiveJobsById((current) => (detail.id in current ? { ...current, [detail.id]: detail } : current))
        }
      })
      .catch((failure) => {
        if (controller.signal.aborted) return
        setSelectedFetchFailedId(selectedJobId)
        setError(errorMessage(failure))
      })

    return () => controller.abort()
  }, [api, needsSelectedFetch, selectedJobId])

  const activeJobs = useMemo(
    () => collectActiveJobs(overview, jobs, liveJobsById),
    [jobs, liveJobsById, overview],
  )

  const liveJob = useMemo<OptimizationJobDetail | null>(() => {
    if (selectedJobId && liveJobsById[selectedJobId]) {
      return liveJobsById[selectedJobId]
    }
    if (activeJobs.length > 0) {
      return activeJobs[0]
    }
    const settled = Object.values(liveJobsById).sort((a, b) =>
      (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at),
    )
    return settled[0] ?? null
  }, [activeJobs, liveJobsById, selectedJobId])

  const activeJobIdsKey = useMemo(
    () => activeJobs.map((job) => job.id).sort().join(','),
    [activeJobs],
  )

  // Clean up all SSE streams on unmount or api instance swap.
  useEffect(() => {
    const controllers = streamControllersRef.current
    const fatalIds = fatalStreamIdsRef.current
    return () => {
      for (const controller of controllers.values()) controller.abort()
      controllers.clear()
      fatalIds.clear()
    }
  }, [api])

  // Subscribe to authenticated SSE streams for every active job without tearing down on filter changes.
  useEffect(() => {
    const activeIds = activeJobIdsKey ? activeJobIdsKey.split(',') : []
    const activeSet = new Set(activeIds)
    const controllers = streamControllersRef.current

    for (const [jobId, controller] of controllers.entries()) {
      if (!activeSet.has(jobId)) {
        controller.abort()
        controllers.delete(jobId)
      }
    }

    for (const jobId of activeIds) {
      if (controllers.has(jobId) || fatalStreamIdsRef.current.has(jobId)) continue
      const controller = new AbortController()
      controllers.set(jobId, controller)

      void runOptimizationEventStream({
        api,
        jobId,
        signal: controller.signal,
        onConnected: () => {
          setStreamWarning('')
          setError((current) => (current === SSE_POLL_FALLBACK_WARNING ? '' : current))
        },
        onUpdate: applyJobUpdate,
        onTerminal: (detail) => {
          setStreamWarning('')
          setError((current) => (current === SSE_POLL_FALLBACK_WARNING ? '' : current))
          setAnnouncement(terminalAnnouncement(detail))
          void loadSnapshot()
        },
        onFallbackWarning: (warning) => {
          setStreamWarning(warning)
          setError((current) => current || warning)
          setAnnouncement(warning)
        },
        onFatalError: (message) => {
          fatalStreamIdsRef.current.add(jobId)
          setStreamWarning('')
          setError(message)
        },
      }).finally(() => {
        if (controllers.get(jobId) === controller) {
          controllers.delete(jobId)
        }
      })
    }
  }, [activeJobIdsKey, api, applyJobUpdate, loadSnapshot])

  const trackStartedJob = useCallback((job: OptimizationJobDetail) => {
    fatalStreamIdsRef.current.delete(job.id)
    setLiveJobsById((current) => ({ ...current, [job.id]: job }))
    setFetchedSelectedJob(job)
    setSelectedFetchFailedId(null)
    setSelectedJobId(job.id)
    setJobs((current) =>
      upsertJobForFilters(
        current,
        job,
        filtersRef.current.targetFilter,
        filtersRef.current.statusFilter,
      ),
    )
    setAnnouncement(`Started ${job.target} optimization run.`)
    void loadSnapshot()
  }, [loadSnapshot])

  const dismissBanner = useCallback((jobId?: string) => {
    setLiveJobsById((current) => {
      if (jobId) {
        if (!(jobId in current)) return current
        const next = { ...current }
        delete next[jobId]
        return next
      }
      const next: Record<string, OptimizationJobDetail> = {}
      for (const [id, detail] of Object.entries(current)) {
        if (isActiveOptimizationStatus(detail.status)) next[id] = detail
      }
      return next
    })
  }, [])

  return {
    overview,
    datasets,
    jobs,
    selectedJobId,
    setSelectedJobId,
    selectedJob,
    liveJob,
    activeJobs,
    streamWarning,
    loading,
    loadingSelected,
    error,
    setError,
    announcement,
    setAnnouncement,
    targetFilter,
    setTargetFilter,
    statusFilter,
    setStatusFilter,
    refreshAll,
    trackStartedJob,
    dismissBanner,
  }
}
