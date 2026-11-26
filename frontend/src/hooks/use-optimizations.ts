import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
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

function upsertJob(list: OptimizationJobSummary[], updated: OptimizationJobSummary): OptimizationJobSummary[] {
  const index = list.findIndex((item) => item.id === updated.id)
  if (index === -1) return [updated, ...list]
  const next = list.slice()
  next[index] = updated
  return next
}

async function consumeSseStream(
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

export function useOptimizations(api: FleetApi) {
  const [overview, setOverview] = useState<OptimizationOverview | null>(null)
  const [datasets, setDatasets] = useState<OptimizationDatasetSummary[] | null>(null)
  const [jobs, setJobs] = useState<OptimizationJobSummary[] | null>(null)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [selectedJob, setSelectedJob] = useState<OptimizationJobDetail | null>(null)
  const [liveJob, setLiveJob] = useState<OptimizationJobDetail | null>(null)
  const [targetFilter, setTargetFilter] = useState<TargetFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [loading, setLoading] = useState(true)
  const [loadingSelected, setLoadingSelected] = useState(false)
  const [error, setError] = useState('')
  const [announcement, setAnnouncement] = useState('')

  const refreshControllerRef = useRef<AbortController | null>(null)
  const selectedControllerRef = useRef<AbortController | null>(null)
  const settledBannerRef = useRef<string | null>(null)

  const refreshAll = useCallback(
    async (opts?: { silent?: boolean }) => {
      refreshControllerRef.current?.abort()
      const controller = new AbortController()
      refreshControllerRef.current = controller
      if (!opts?.silent) setLoading(true)
      try {
        const [nextOverview, nextDatasets, nextJobs] = await Promise.all([
          api.getOptimizationOverview(controller.signal),
          api.listOptimizationDatasets(controller.signal),
          api.listOptimizationJobs(
            {
              target: targetFilter === 'all' ? undefined : targetFilter,
              status: statusFilter === 'all' ? undefined : statusFilter,
              limit: 50,
            },
            controller.signal,
          ),
        ])
        if (controller.signal.aborted) return
        setError('')
        setOverview(nextOverview)
        setDatasets(nextDatasets)
        setJobs(nextJobs)
        setSelectedJobId((current) => {
          if (current && nextJobs.some((item) => item.id === current)) return current
          const active = nextOverview.targets.find((item) => item.active_job)?.active_job
          return active?.id ?? nextJobs[0]?.id ?? null
        })
      } catch (failure) {
        if (!controller.signal.aborted) setError(errorMessage(failure))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    },
    [api, statusFilter, targetFilter],
  )

  useEffect(() => {
    const controller = new AbortController()
    refreshControllerRef.current?.abort()
    refreshControllerRef.current = controller
    void Promise.all([
      api.getOptimizationOverview(controller.signal),
      api.listOptimizationDatasets(controller.signal),
      api.listOptimizationJobs(
        {
          target: targetFilter === 'all' ? undefined : targetFilter,
          status: statusFilter === 'all' ? undefined : statusFilter,
          limit: 50,
        },
        controller.signal,
      ),
    ])
      .then(([nextOverview, nextDatasets, nextJobs]) => {
        if (controller.signal.aborted) return
        setError('')
        setOverview(nextOverview)
        setDatasets(nextDatasets)
        setJobs(nextJobs)
        setSelectedJobId((current) => {
          if (current && nextJobs.some((item) => item.id === current)) return current
          const active = nextOverview.targets.find((item) => item.active_job)?.active_job
          return active?.id ?? nextJobs[0]?.id ?? null
        })
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [api, statusFilter, targetFilter])

  // Load selected job detail whenever selection changes.
  useEffect(() => {
    selectedControllerRef.current?.abort()
    if (!selectedJobId) return
    if (liveJob && liveJob.id === selectedJobId) return
    const controller = new AbortController()
    selectedControllerRef.current = controller
    void api
      .getOptimizationJob(selectedJobId, controller.signal)
      .then((detail) => {
        if (!controller.signal.aborted) {
          setSelectedJob(detail)
          setLoadingSelected(false)
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(failure))
          setLoadingSelected(false)
        }
      })
    return () => controller.abort()
  }, [api, liveJob, selectedJobId])

  const activeJobId = useMemo(() => {
    const activeFromOverview = overview?.targets.find((item) => item.active_job)?.active_job?.id
    if (activeFromOverview) return activeFromOverview
    const activeFromList = jobs?.find((item) => item.status === 'queued' || item.status === 'running')?.id
    if (activeFromList) return activeFromList
    if (liveJob && (liveJob.status === 'queued' || liveJob.status === 'running')) return liveJob.id
    return null
  }, [jobs, liveJob, overview])

  // Subscribe to authenticated SSE updates while an active job runs.
  useEffect(() => {
    if (!activeJobId) return
    const controller = new AbortController()
    let retries = 0

    const connect = async () => {
      while (!controller.signal.aborted && retries <= 3) {
        try {
          const response = await api.openOptimizationEvents(activeJobId, controller.signal)
          let reachedTerminal = false
          await consumeSseStream(response, controller.signal, (eventName, detail) => {
            setLiveJob(detail)
            setJobs((current) => (current ? upsertJob(current, detail) : [detail]))
            setSelectedJob((current) => (current && current.id === detail.id ? detail : current))
            if (eventName === 'done' || detail.status === 'completed' || detail.status === 'failed' || detail.status === 'cancelled') {
              reachedTerminal = true
              settledBannerRef.current = detail.id
              const label =
                detail.status === 'completed'
                  ? `Optimization ${detail.target} completed.`
                  : detail.status === 'cancelled'
                    ? `Optimization ${detail.target} cancelled.`
                    : `Optimization ${detail.target} failed.`
              setAnnouncement(label)
              void refreshAll({ silent: true })
            }
          })
          if (reachedTerminal || controller.signal.aborted) break
        } catch {
          if (controller.signal.aborted) break
        }
        retries += 1
        const delayMs = 1000 * 2 ** (retries - 1)
        await new Promise((resolve) => setTimeout(resolve, delayMs))
      }
    }

    void connect()
    return () => controller.abort()
  }, [activeJobId, api, refreshAll])

  const trackStartedJob = useCallback((job: OptimizationJobDetail) => {
    settledBannerRef.current = null
    setLiveJob(job)
    setSelectedJobId(job.id)
    setSelectedJob(job)
    setJobs((current) => (current ? upsertJob(current, job) : [job]))
    setAnnouncement(`Started ${job.target} optimization run.`)
    void refreshAll({ silent: true })
  }, [refreshAll])

  const dismissBanner = useCallback(() => {
    settledBannerRef.current = null
    setLiveJob(null)
  }, [])

  return {
    overview,
    datasets,
    jobs,
    selectedJobId,
    setSelectedJobId,
    selectedJob,
    liveJob,
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
