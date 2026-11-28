import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ActiveRunBanner } from '@/components/optimization/ActiveRunBanner'
import { ModelCombobox } from '@/components/optimization/ModelCombobox'
import { TooltipProvider } from '@/components/ui/tooltip'
import {
  ApiError,
  type FleetApi,
  type OptimizationDatasetSummary,
  type OptimizationJobDetail,
  type OptimizationJobSummary,
  type OptimizationOverview,
} from '@/lib/api'

// Minimal React hook runner so we can execute the real `useOptimizations` hook
// (state, refs, memos, callbacks, effects, and effect cleanups) in Node Vitest
// without pulling in jsdom, while leaving real React untouched for SSR renders.
interface EffectEntry {
  deps: readonly unknown[] | undefined
  cleanup: (() => void) | void
  fn: () => void | (() => void)
  pending: boolean
}

interface HookHarness {
  cursor: number
  states: Map<number, unknown>
  refs: Map<number, { current: unknown }>
  memos: Map<number, { value: unknown; deps: readonly unknown[] | undefined }>
  effects: Map<number, EffectEntry>
  needsRender: boolean
}

let activeHarness: HookHarness | null = null

function depsChanged(prev: readonly unknown[] | undefined, next: readonly unknown[] | undefined): boolean {
  if (!prev || !next) return true
  if (prev.length !== next.length) return true
  for (let i = 0; i < prev.length; i += 1) {
    if (!Object.is(prev[i], next[i])) return true
  }
  return false
}

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react')>()
  return {
    ...actual,
    useState<T>(initial: T | (() => T)) {
      if (!activeHarness) return actual.useState(initial)
      const h = activeHarness
      const idx = h.cursor++
      if (!h.states.has(idx)) {
        h.states.set(idx, typeof initial === 'function' ? (initial as () => T)() : initial)
      }
      const setState = (next: T | ((prev: T) => T)) => {
        const prev = h.states.get(idx) as T
        const resolved = typeof next === 'function' ? (next as (p: T) => T)(prev) : next
        if (!Object.is(prev, resolved)) {
          h.states.set(idx, resolved)
          h.needsRender = true
        }
      }
      return [h.states.get(idx) as T, setState] as const
    },
    useRef<T>(initial: T) {
      if (!activeHarness) return actual.useRef(initial)
      const h = activeHarness
      const idx = h.cursor++
      if (!h.refs.has(idx)) {
        h.refs.set(idx, { current: initial })
      }
      return h.refs.get(idx) as { current: T }
    },
    useMemo<T>(factory: () => T, deps: readonly unknown[]) {
      if (!activeHarness) return actual.useMemo(factory, deps)
      const h = activeHarness
      const idx = h.cursor++
      const existing = h.memos.get(idx)
      if (!existing || depsChanged(existing.deps, deps)) {
        h.memos.set(idx, { value: factory(), deps })
      }
      return h.memos.get(idx)!.value as T
    },
    useCallback<T extends (...args: never[]) => unknown>(fn: T, deps: readonly unknown[]) {
      if (!activeHarness) return actual.useCallback(fn, deps)
      const h = activeHarness
      const idx = h.cursor++
      const existing = h.memos.get(idx)
      if (!existing || depsChanged(existing.deps, deps)) {
        h.memos.set(idx, { value: fn, deps })
      }
      return h.memos.get(idx)!.value as T
    },
    useEffect(fn: () => void | (() => void), deps?: readonly unknown[]) {
      if (!activeHarness) return actual.useEffect(fn, deps)
      const h = activeHarness
      const idx = h.cursor++
      const existing = h.effects.get(idx)
      if (!existing) {
        h.effects.set(idx, { deps, cleanup: undefined, fn, pending: true })
      } else if (depsChanged(existing.deps, deps)) {
        existing.deps = deps
        existing.fn = fn
        existing.pending = true
      }
    },
  }
})

// Import hook AFTER vi.mock('react') declaration (Vitest hoists vi.mock automatically).
import {
  POLL_FALLBACK_INTERVAL_MS,
  SSE_POLL_FALLBACK_WARNING,
  runOptimizationEventStream,
  useOptimizations,
} from './use-optimizations'

function mountHook<T>(hookFn: () => T) {
  const harness: HookHarness = {
    cursor: 0,
    states: new Map(),
    refs: new Map(),
    memos: new Map(),
    effects: new Map(),
    needsRender: true,
  }
  let current!: T

  const renderOnce = () => {
    harness.cursor = 0
    harness.needsRender = false
    activeHarness = harness
    try {
      current = hookFn()
    } finally {
      activeHarness = null
    }
    for (const effect of harness.effects.values()) {
      if (effect.pending) {
        effect.pending = false
        if (typeof effect.cleanup === 'function') {
          effect.cleanup()
        }
        effect.cleanup = effect.fn()
      }
    }
  }

  const settle = async (rounds = 8) => {
    for (let i = 0; i < rounds; i += 1) {
      let guard = 0
      while (harness.needsRender && guard < 20) {
        renderOnce()
        guard += 1
      }
      await Promise.resolve()
    }
    while (harness.needsRender) {
      renderOnce()
    }
  }

  renderOnce()

  return {
    get current() {
      return current
    },
    settle,
    unmount() {
      for (const effect of harness.effects.values()) {
        if (typeof effect.cleanup === 'function') {
          effect.cleanup()
          effect.cleanup = undefined
        }
      }
    },
  }
}

function makeJob(overrides: Partial<OptimizationJobDetail> = {}): OptimizationJobDetail {
  return {
    id: 'job-active',
    target: 'memory',
    program_kind: null,
    status: 'running',
    stage: 'reanchor_cv',
    dataset_source: 'builtin',
    dataset_name: 'memory.jsonl',
    dataset_id: null,
    config_json: { branch_mode: 'configured_branch' },
    progress_json: { stage: 'reanchor_cv', label: 'Running CV', completed: 5, total: 20 },
    saved: null,
    baseline_score: 0.72,
    optimized_score: null,
    train_size: 70,
    val_size: 0,
    holdout_size: 30,
    artifact_object_key: null,
    artifact_sha256: null,
    artifact_size_bytes: null,
    resolved_decision_model: 'typesafe:jev-1.14.0',
    mlflow_run_id: null,
    mlflow_experiment: null,
    mlflow_run_url: null,
    cancel_requested: false,
    error: null,
    created_at: '2026-11-26T08:00:00Z',
    started_at: '2026-11-26T08:00:02Z',
    finished_at: null,
    report_json: null,
    log_tail: '[memory] cv tick 5/20',
    ...overrides,
  }
}

function makeOverview(activeJob: OptimizationJobSummary | null = null): OptimizationOverview {
  return {
    targets: [
      {
        target: 'skills',
        source: 'default',
        env_path: null,
        active_promotion: null,
        can_rollback_previous: false,
        can_rollback_env: false,
        latest_job: null,
        active_job: activeJob?.target === 'skills' ? activeJob : null,
      },
      {
        target: 'memory',
        source: 'default',
        env_path: null,
        active_promotion: null,
        can_rollback_previous: false,
        can_rollback_env: false,
        latest_job: null,
        active_job: activeJob?.target === 'memory' ? activeJob : null,
      },
      {
        target: 'rlm',
        source: 'default',
        env_path: null,
        active_promotion: null,
        can_rollback_previous: false,
        can_rollback_env: false,
        latest_job: null,
        active_job: activeJob?.target === 'rlm' ? activeJob : null,
      },
    ],
    capabilities: {
      auto_neon_enabled: false,
      configured_branch_enabled: true,
      configured_branch_label: 'ep-opt/neondb',
      mlflow_tracking_uri: null,
      mlflow_experiment: 'fleet-rlm',
      daytona_configured: true,
      decision_model: 'typesafe:jev-1.14.0',
      llm_model: 'openai/gpt-5',
      sub_llm_model: 'openai/gpt-5-mini',
    },
  }
}

function makeSseResponse(chunks: Array<{ event?: string; data: unknown }>, holdOpen = false) {
  const encoder = new TextEncoder()
  let controllerRef: ReadableStreamDefaultController<Uint8Array> | null = null
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controllerRef = controller
      for (const chunk of chunks) {
        const prefix = chunk.event ? `event: ${chunk.event}\n` : ''
        controller.enqueue(encoder.encode(`${prefix}data: ${JSON.stringify(chunk.data)}\n\n`))
      }
      if (!holdOpen) controller.close()
    },
  })
  const response = new Response(stream, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
  return {
    response,
    push(event: string | undefined, data: unknown) {
      if (!controllerRef) return
      const prefix = event ? `event: ${event}\n` : ''
      controllerRef.enqueue(encoder.encode(`${prefix}data: ${JSON.stringify(data)}\n\n`))
    },
    close() {
      controllerRef?.close()
    },
  }
}

describe('useOptimizations hook behavior', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('syncs selectedJob immediately when switching from an older run back to the active run, tracks loadingSelected, and clears selectedJob when filters yield 0 rows', async () => {
    const activeJob = makeJob({
      id: 'job-active',
      status: 'running',
      progress_json: { stage: 'reanchor_cv', completed: 8, total: 20 },
    })
    const activeJobUpdated = makeJob({
      id: 'job-active',
      status: 'running',
      progress_json: { stage: 'holdout_eval', completed: 19, total: 20 },
      log_tail: '[memory] holdout 19/20',
    })
    const oldJob = makeJob({
      id: 'job-old',
      status: 'completed',
      stage: 'completed',
      optimized_score: 0.85,
      report_json: { eligible: true },
      created_at: '2026-11-25T10:00:00Z',
    })

    let resolveOldJob!: (detail: OptimizationJobDetail) => void
    const oldJobPromise = new Promise<OptimizationJobDetail>((resolve) => {
      resolveOldJob = resolve
    })

    const sse = makeSseResponse([{ event: 'progress', data: activeJobUpdated }], true)
    const getOptimizationOverview = vi.fn(async () => makeOverview(activeJob))
    const listOptimizationDatasets = vi.fn(async (): Promise<OptimizationDatasetSummary[]> => [])
    const listOptimizationJobs = vi.fn(async (filters?: { status?: string }) => {
      if (filters?.status === 'failed') return []
      return [activeJob, oldJob]
    })
    const getOptimizationJob = vi.fn(async (id: string) => {
      if (id === 'job-old') return oldJobPromise
      return activeJobUpdated
    })
    const openOptimizationEvents = vi.fn(async () => sse.response)

    const api = {
      getOptimizationOverview,
      listOptimizationDatasets,
      listOptimizationJobs,
      getOptimizationJob,
      openOptimizationEvents,
    } as unknown as FleetApi

    const hook = mountHook(() => useOptimizations(api))
    await hook.settle()

    // Active run received SSE update and populated activeJobs + selectedJob.
    expect(hook.current.selectedJobId).toBe('job-active')
    expect(hook.current.selectedJob?.id).toBe('job-active')
    expect(hook.current.selectedJob?.progress_json.completed).toBe(19)
    expect(hook.current.activeJobs).toHaveLength(1)

    // Switch to older run: loadingSelected turns true while getOptimizationJob is pending.
    hook.current.setSelectedJobId('job-old')
    await hook.settle()
    expect(hook.current.loadingSelected).toBe(true)

    resolveOldJob(oldJob)
    await hook.settle()
    expect(hook.current.loadingSelected).toBe(false)
    expect(hook.current.selectedJob?.id).toBe('job-old')

    // Switch back to the active run: selectedJob syncs IMMEDIATELY to liveJob (never stuck on job-old).
    hook.current.setSelectedJobId('job-active')
    await hook.settle()
    expect(hook.current.loadingSelected).toBe(false)
    expect(hook.current.selectedJob?.id).toBe('job-active')
    expect(hook.current.selectedJob?.log_tail).toBe('[memory] holdout 19/20')

    // Filter to a status with 0 rows: clears selectedJobId and selectedJob cleanly.
    hook.current.setStatusFilter('failed')
    await hook.settle()
    expect(hook.current.jobs).toEqual([])
    expect(hook.current.selectedJobId).toBeNull()
    expect(hook.current.selectedJob).toBeNull()

    hook.unmount()
  })

  it('does not abort SSE or refetch overview/datasets when changing table filters', async () => {
    const activeJob = makeJob({ id: 'job-live', target: 'skills', status: 'running' })
    const sse = makeSseResponse([{ event: 'progress', data: activeJob }], true)
    let streamSignal: AbortSignal | undefined

    const getOptimizationOverview = vi.fn(async () => makeOverview(activeJob))
    const listOptimizationDatasets = vi.fn(async (): Promise<OptimizationDatasetSummary[]> => [])
    const listOptimizationJobs = vi.fn(async () => [activeJob])
    const getOptimizationJob = vi.fn(async () => activeJob)
    const openOptimizationEvents = vi.fn(async (_id: string, signal?: AbortSignal) => {
      streamSignal = signal
      return sse.response
    })

    const api = {
      getOptimizationOverview,
      listOptimizationDatasets,
      listOptimizationJobs,
      getOptimizationJob,
      openOptimizationEvents,
    } as unknown as FleetApi

    const hook = mountHook(() => useOptimizations(api))
    await hook.settle()

    expect(openOptimizationEvents).toHaveBeenCalledTimes(1)
    expect(getOptimizationOverview).toHaveBeenCalledTimes(1)
    expect(listOptimizationDatasets).toHaveBeenCalledTimes(1)
    expect(listOptimizationJobs).toHaveBeenCalledTimes(1)
    expect(streamSignal?.aborted).toBe(false)

    // Change target and status filters multiple times.
    hook.current.setTargetFilter('memory')
    await hook.settle()
    hook.current.setStatusFilter('completed')
    await hook.settle()

    // SSE stream stayed alive without aborting or reconnecting!
    expect(openOptimizationEvents).toHaveBeenCalledTimes(1)
    expect(streamSignal?.aborted).toBe(false)

    // Overview and datasets were NOT refetched on filter changes — only jobs list!
    expect(getOptimizationOverview).toHaveBeenCalledTimes(1)
    expect(listOptimizationDatasets).toHaveBeenCalledTimes(1)
    expect(listOptimizationJobs).toHaveBeenCalledTimes(3)

    hook.unmount()
    expect(streamSignal?.aborted).toBe(true)
  })

  it.each(['initial', 'manual'])('keeps newer filtered results during a delayed %s refresh', async (kind) => {
    const oldJob = makeJob({ id: 'old', status: 'completed' })
    const filteredJob = makeJob({ id: 'filtered', status: 'failed' })
    let resolveOverview!: (overview: OptimizationOverview) => void
    const pendingOverview = new Promise<OptimizationOverview>((resolve) => {
      resolveOverview = resolve
    })
    const getOptimizationOverview = vi.fn(() => pendingOverview)
    if (kind === 'manual') getOptimizationOverview.mockResolvedValueOnce(makeOverview())
    const listOptimizationJobs = vi.fn(async ({ status }: { status?: string }) =>
      status === 'failed' ? [filteredJob] : [oldJob],
    )
    const api = {
      getOptimizationOverview,
      listOptimizationDatasets: vi.fn(async () => []),
      listOptimizationJobs,
      getOptimizationJob: vi.fn(async (id: string) =>
        id === filteredJob.id ? filteredJob : oldJob,
      ),
      openOptimizationEvents: vi.fn(),
    } as unknown as FleetApi
    const hook = mountHook(() => useOptimizations(api))
    await hook.settle()
    const refresh = kind === 'manual' ? hook.current.refreshAll() : undefined

    hook.current.setStatusFilter('failed')
    await hook.settle()
    expect(hook.current.jobs).toEqual([filteredJob])
    expect(hook.current.selectedJobId).toBe(filteredJob.id)

    resolveOverview(makeOverview())
    await refresh
    await hook.settle()
    expect(hook.current.overview).not.toBeNull()
    expect(hook.current.loading).toBe(false)
    expect(hook.current.jobs).toEqual([filteredJob])
    expect(hook.current.selectedJobId).toBe(filteredJob.id)
    hook.unmount()
  })
})

describe('runOptimizationEventStream SSE reliability', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('processes a terminal snapshot and its done frame once', async () => {
    const detail = makeJob({ status: 'completed' })
    const { response } = makeSseResponse([
      { event: 'snapshot', data: detail },
      { event: 'done', data: detail },
    ])
    const onUpdate = vi.fn()
    const onTerminal = vi.fn()

    const outcome = await runOptimizationEventStream({
      api: {
        openOptimizationEvents: vi.fn(async () => response),
        getOptimizationJob: vi.fn(),
      },
      jobId: detail.id,
      signal: new AbortController().signal,
      onUpdate,
      onTerminal,
      onFallbackWarning: vi.fn(),
      onFatalError: vi.fn(),
    })

    expect(outcome).toBe('terminal')
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith(detail)
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith(detail)
  })

  it('resets retry counter upon receiving any healthy event across repeated stream drops', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const updates: OptimizationJobDetail[] = []
    const onTerminal = vi.fn()
    const onFallbackWarning = vi.fn()
    const onFatalError = vi.fn()

    let attempt = 0
    const openOptimizationEvents = vi.fn(async () => {
      attempt += 1
      if (attempt <= 4) {
        // Each connection delivers 1 healthy event then closes (simulating proxy timeout on a long run).
        return makeSseResponse([
          {
            event: 'progress',
            data: makeJob({
              id: 'job-long',
              status: 'running',
              progress_json: { stage: 'gepa_compile', completed: attempt, total: 5 },
            }),
          },
        ]).response
      }
      return makeSseResponse([
        {
          event: 'done',
          data: makeJob({ id: 'job-long', status: 'completed', stage: 'completed' }),
        },
      ]).response
    })

    const api = {
      openOptimizationEvents,
      getOptimizationJob: vi.fn(),
    }

    const promise = runOptimizationEventStream({
      api,
      jobId: 'job-long',
      signal: controller.signal,
      maxRetries: 2,
      baseDelayMs: 100,
      onUpdate: (detail) => updates.push(detail),
      onTerminal,
      onFallbackWarning,
      onFatalError,
    })

    // 4 drops after healthy events exceed maxRetries=2 cumulatively, but because each healthy
    // event resets retries=0, all 5 connections succeed without ever falling back to polling!
    await vi.advanceTimersByTimeAsync(1000)
    const outcome = await promise

    expect(outcome).toBe('terminal')
    expect(openOptimizationEvents).toHaveBeenCalledTimes(5)
    expect(updates).toHaveLength(5)
    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(onFallbackWarning).not.toHaveBeenCalled()
    expect(onFatalError).not.toHaveBeenCalled()
  })

  it('handles 401/403/404 immediately without blind retries or polling', async () => {
    vi.useFakeTimers()
    for (const status of [401, 403, 404]) {
      const controller = new AbortController()
      const onFatalError = vi.fn()
      const onFallbackWarning = vi.fn()
      const openOptimizationEvents = vi.fn(async () => {
        throw new ApiError(status, `HTTP ${status} failure`)
      })
      const getOptimizationJob = vi.fn()

      const outcome = await runOptimizationEventStream({
        api: { openOptimizationEvents, getOptimizationJob },
        jobId: 'job-auth',
        signal: controller.signal,
        onUpdate: vi.fn(),
        onTerminal: vi.fn(),
        onFallbackWarning,
        onFatalError,
      })

      expect(outcome).toBe('fatal')
      expect(openOptimizationEvents).toHaveBeenCalledTimes(1)
      expect(getOptimizationJob).not.toHaveBeenCalled()
      expect(onFallbackWarning).not.toHaveBeenCalled()
      expect(onFatalError).toHaveBeenCalledWith(`HTTP ${status} failure`)
    }
  })

  it('surfaces fallback warning and polls every 5s until terminal status after exhausting SSE retries', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const onFallbackWarning = vi.fn()
    const onTerminal = vi.fn()
    const updates: OptimizationJobDetail[] = []

    const openOptimizationEvents = vi.fn(async () => {
      throw new ApiError(502, 'Bad Gateway')
    })
    let pollCalls = 0
    const getOptimizationJob = vi.fn(async () => {
      pollCalls += 1
      return makeJob({
        id: 'job-poll',
        status: pollCalls >= 2 ? 'completed' : 'running',
        stage: pollCalls >= 2 ? 'completed' : 'gepa_compile',
      })
    })

    const promise = runOptimizationEventStream({
      api: { openOptimizationEvents, getOptimizationJob },
      jobId: 'job-poll',
      signal: controller.signal,
      maxRetries: 3,
      baseDelayMs: 1000,
      pollIntervalMs: POLL_FALLBACK_INTERVAL_MS,
      onUpdate: (detail) => updates.push(detail),
      onTerminal,
      onFallbackWarning,
      onFatalError: vi.fn(),
    })

    // Exhaust initial attempt + 3 retries (1s + 2s + 4s = 7s).
    await vi.advanceTimersByTimeAsync(1000 + 2000 + 4000)
    expect(openOptimizationEvents).toHaveBeenCalledTimes(4)
    expect(onFallbackWarning).toHaveBeenCalledWith(SSE_POLL_FALLBACK_WARNING)

    // 1st poll after 5s -> still running; 2nd poll after another 5s -> completed!
    await vi.advanceTimersByTimeAsync(POLL_FALLBACK_INTERVAL_MS)
    expect(getOptimizationJob).toHaveBeenCalledTimes(1)
    expect(onTerminal).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(POLL_FALLBACK_INTERVAL_MS)
    const outcome = await promise

    expect(outcome).toBe('terminal')
    expect(getOptimizationJob).toHaveBeenCalledTimes(2)
    expect(updates).toHaveLength(2)
    expect(onTerminal).toHaveBeenCalledTimes(1)
  })
})

describe('ModelCombobox & ActiveRunBanner static markup', () => {
  beforeEach(() => {
    vi.stubGlobal('window', {
      matchMedia: () => ({
        matches: true,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
      addEventListener: () => {},
      removeEventListener: () => {},
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders accessible ModelCombobox input, combobox ARIA attributes, and refresh trigger', () => {
    const html = renderToStaticMarkup(
      createElement(
        TooltipProvider,
        null,
        createElement(ModelCombobox, {
          id: 'root-lm-combobox',
          ariaLabel: 'Root LM model',
          value: 'openai/gpt-5',
          onChange: () => {},
          models: [
            { id: 'openai/gpt-5', label: 'GPT-5', owned_by: 'openai' },
            { id: 'openai/gpt-5-mini', label: 'GPT-5 Mini', owned_by: 'openai' },
          ],
          onRefresh: () => {},
          placeholder: 'Select root LM',
        }),
      ),
    )

    expect(html).toContain('id="root-lm-combobox"')
    expect(html).toContain('role="combobox"')
    expect(html).toContain('aria-autocomplete="list"')
    expect(html).toContain('aria-label="Root LM model"')
    expect(html).toContain('value="openai/gpt-5"')
    expect(html).toContain('aria-label="Refresh models"')
    expect(html).toContain('aria-label="Browse models"')
  })

  it('omits Daytona sandbox stage for flex/local runs, renders timeline durations, and drops Moonwalking', () => {
    const flexFailedJob = makeJob({
      id: 'job-flex-fail',
      target: 'rlm',
      program_kind: 'flex',
      status: 'failed',
      stage: 'failed',
      error: 'Metric timeout',
      config_json: { program_kind: 'flex', branch_mode: 'configured_branch' },
      progress_json: {
        stage: 'failed',
        label: 'Failed during GEPA prompt evolution',
        stage_timeline: [
          { stage: 'loading_dataset', duration_seconds: 1.4 },
          { stage: 'baseline_eval', duration_seconds: 12.2 },
          { stage: 'gepa_compile', duration_seconds: 42 },
        ],
      } as OptimizationJobDetail['progress_json'],
    })

    const html = renderToStaticMarkup(
      createElement(
        TooltipProvider,
        null,
        createElement(ActiveRunBanner, {
          job: flexFailedJob,
          onCancel: async () => {},
          onInspect: () => {},
          onDismiss: () => {},
        }),
      ),
    )

    expect(html).not.toContain('Provision Daytona sandboxes')
    expect(html).not.toContain('Moonwalking')
    expect(html).toContain('Dismiss run banner')
    expect(html).toContain('1.4s')
    expect(html).toContain('42s · Failed')
  })
})
