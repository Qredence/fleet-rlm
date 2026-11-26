import { MotionConfig } from 'motion/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ActiveRunBanner } from '@/components/optimization/ActiveRunBanner'
import { CliReferenceSection } from '@/components/optimization/CliReferenceSection'
import { DatasetsSection } from '@/components/optimization/DatasetsSection'
import { RunHistorySection } from '@/components/optimization/RunHistorySection'
import { RunInspectorPanel } from '@/components/optimization/RunInspectorPanel'
import { TargetSummaryGrid } from '@/components/optimization/TargetSummaryGrid'
import { SidebarProvider } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import {
  createApi,
  type OptimizationDatasetSummary,
  type OptimizationJobDetail,
  type OptimizationOverview,
} from '@/lib/api'
import { OptimizationPage } from './OptimizationPage'

const MOCK_OVERVIEW: OptimizationOverview = {
  targets: [
    {
      target: 'skills',
      source: 'default',
      env_path: null,
      active_promotion: null,
      can_rollback_previous: false,
      can_rollback_env: false,
      latest_job: null,
      active_job: null,
    },
    {
      target: 'memory',
      source: 'promoted',
      env_path: 'artifacts/memory_gate.json',
      active_promotion: {
        id: 'promo-1',
        target: 'memory',
        job_id: 'job-1',
        artifact_object_key: 'optimizations/artifacts/job-1/memory.json',
        artifact_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
        artifact_size_bytes: 1024,
        resolved_decision_model: 'typesafe:jev-1.14.0',
        active: true,
        promoted_at: '2026-11-26T00:00:00Z',
        rolled_back_at: null,
      },
      can_rollback_previous: false,
      can_rollback_env: true,
      latest_job: {
        id: 'job-1',
        target: 'memory',
        program_kind: null,
        status: 'completed',
        stage: 'completed',
        dataset_source: 'builtin',
        dataset_name: 'memory.jsonl',
        dataset_id: null,
        config_json: { branch_mode: 'configured_branch' },
        progress_json: { stage: 'completed', label: 'Completed' },
        saved: true,
        baseline_score: 0.7714,
        optimized_score: 0.8286,
        train_size: 70,
        val_size: 0,
        holdout_size: 30,
        artifact_object_key: 'optimizations/artifacts/job-1/memory.json',
        artifact_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
        artifact_size_bytes: 1024,
        resolved_decision_model: 'typesafe:jev-1.14.0',
        mlflow_run_id: 'run-123',
        mlflow_experiment: 'fleet-rlm',
        mlflow_run_url: 'http://127.0.0.1:5001/#/experiments/41/runs/run-123',
        cancel_requested: false,
        error: null,
        created_at: '2026-11-26T00:00:00Z',
        started_at: '2026-11-26T00:00:01Z',
        finished_at: '2026-11-26T00:01:00Z',
      },
      active_job: null,
    },
    {
      target: 'rlm',
      source: 'env',
      env_path: 'artifacts/rlm_program.json',
      active_promotion: null,
      can_rollback_previous: false,
      can_rollback_env: false,
      latest_job: null,
      active_job: null,
    },
  ],
  capabilities: {
    auto_neon_enabled: false,
    configured_branch_enabled: true,
    configured_branch_label: 'ep-branch/neondb',
    mlflow_tracking_uri: 'http://127.0.0.1:5001',
    mlflow_experiment: 'fleet-rlm',
    daytona_configured: true,
    decision_model: 'typesafe:jev-1.14.0',
    llm_model: 'openai/gpt-5',
    sub_llm_model: 'openai/gpt-5-mini',
  },
}

const MOCK_DATASETS: OptimizationDatasetSummary[] = [
  {
    id: 'builtin:memory',
    name: 'memory.jsonl',
    target: 'memory',
    source: 'builtin',
    row_count: 100,
    size_bytes: 14200,
    sha256: 'abc123',
    summary_json: {
      row_count: 100,
      splits: { train: 70, val: 0, holdout: 30 },
      lineage_count: 24,
      positive_count: 42,
      negative_count: 58,
    },
    created_at: null,
  },
]

const MOCK_JOB_DETAIL: OptimizationJobDetail = {
  ...MOCK_OVERVIEW.targets[1].latest_job!,
  status: 'running',
  stage: 'reanchor_cv',
  progress_json: {
    stage: 'reanchor_cv',
    label: 'Calibrating thresholds & Choice weights via ReAnchor CV',
    completed: 12,
    total: 40,
    baseline_score: 0.7714,
  },
  report_json: {
    eligible: true,
    fp_rate_before: 0.2,
    fp_rate_after: 0.05,
    diagnostics_after: { fn_rate: 0.1, action_accuracy: 0.9, scope_accuracy: 0.95 },
    fitted_parameters: { worth_saving: { threshold: 0.62 } },
  },
  log_tail: '[memory] starting ReAnchor CV...',
}

describe('Optimization Control Plane API client', () => {
  beforeEach(() => {
    vi.stubGlobal('window', globalThis)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends bearer auth headers on optimization endpoints and authenticated SSE stream', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(MOCK_OVERVIEW), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const api = createApi('secret-bearer')
    await api.getOptimizationOverview()
    await api.openOptimizationEvents('job-1')

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/optimizations/overview')
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      Authorization: 'Bearer secret-bearer',
    })
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/optimizations/jobs/job-1/events')
    expect(fetchMock.mock.calls[1]?.[1]?.headers).toMatchObject({
      Authorization: 'Bearer secret-bearer',
      Accept: 'text/event-stream',
    })
  })

  it('posts launch, promote, rollback, and delete requests to expected REST routes', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const api = createApi('secret-bearer')
    await api.createOptimizationJob({ target: 'memory', dataset_id: 'builtin:memory', seed: 0 })
    await api.promoteOptimizationJob('job-1', {
      confirm_non_qualifying: true,
      pin_decision_model: true,
    })
    await api.rollbackOptimizationTarget('memory', { mode: 'previous' })
    await api.deleteOptimizationDataset('custom-1')

    expect(fetchMock.mock.calls.map((call) => [call[0], call[1]?.method])).toEqual([
      ['/api/optimizations/jobs', 'POST'],
      ['/api/optimizations/jobs/job-1/promote', 'POST'],
      ['/api/optimizations/targets/memory/rollback', 'POST'],
      ['/api/optimizations/datasets/custom-1', 'DELETE'],
    ])
  })
})

describe('Optimization Control Plane SSR rendering & Base UI / @fluid structure', () => {
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

  it('renders page shell, target subgrid, active run banner with native progress, history table, datasets, and CLI reference', () => {
    const api = createApi('token')

    const pageHtml = renderToStaticMarkup(
      <MemoryRouter>
        <MotionConfig reducedMotion="user">
          <TooltipProvider>
            <SidebarProvider open={true} onOpenChange={() => {}}>
              <OptimizationPage api={api} />
            </SidebarProvider>
          </TooltipProvider>
        </MotionConfig>
      </MemoryRouter>,
    )
    expect(pageHtml).toContain('Optimization')
    expect(pageHtml).toContain('New Run')
    expect(pageHtml).toContain('Runs')
    expect(pageHtml).toContain('Datasets')
    expect(pageHtml).toContain('CLI &amp; Environment')

    const gridHtml = renderToStaticMarkup(
      <TargetSummaryGrid
        overview={MOCK_OVERVIEW}
        loading={false}
        onLaunchTarget={() => {}}
        onRollbackTarget={async () => {}}
      />,
    )
    expect(gridHtml).toContain('opt-subgrid-cards')
    expect(gridHtml).toContain('Skill Selector')
    expect(gridHtml).toContain('Memory Gate')
    expect(gridHtml).toContain('typesafe:jev-1.14.0')
    expect(gridHtml).toContain('82.9%')
    expect(gridHtml).toContain('Rollback')

    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const bannerHtml = renderToStaticMarkup(
      <MotionConfig reducedMotion="user">
        <ActiveRunBanner
          job={MOCK_JOB_DETAIL}
          onCancel={async () => {}}
          onInspect={() => {}}
          onDismiss={() => {}}
        />
      </MotionConfig>,
    )
    expect(consoleErrorSpy).not.toHaveBeenCalled()
    consoleErrorSpy.mockRestore()
    expect(bannerHtml).toContain('<progress')
    expect(bannerHtml).toContain('opt-progress')
    expect(bannerHtml).toContain('12/40')
    expect(bannerHtml).not.toMatch(/<p\b[^>]*>(?:(?!<\/p>)[\s\S])*<div\b/i)

    const historyHtml = renderToStaticMarkup(
      <RunHistorySection
        overview={MOCK_OVERVIEW}
        jobs={[MOCK_OVERVIEW.targets[1].latest_job!]}
        loading={false}
        selectedJobId="job-1"
        selectedJob={MOCK_JOB_DETAIL}
        loadingSelected={false}
        targetFilter="all"
        statusFilter="all"
        onTargetFilterChange={() => {}}
        onStatusFilterChange={() => {}}
        onSelectJob={() => {}}
        onPromote={async () => {}}
        onRerun={() => {}}
        onLaunchFirst={() => {}}
      />,
    )
    expect(historyHtml).toContain('opt-table-wrapper')
    expect(historyHtml).toContain('role="region"')
    expect(historyHtml).toContain('data-label="Target"')

    const inspectorHtml = renderToStaticMarkup(
      <RunInspectorPanel
        job={MOCK_JOB_DETAIL}
        loading={false}
        activePromotionJobId={null}
        onPromote={async () => {}}
        onRerun={() => {}}
      />,
    )
    expect(inspectorHtml).toContain('MLflow')
    expect(inspectorHtml).toContain('worth_saving')
    expect(inspectorHtml).toContain('Promote')

    const datasetsHtml = renderToStaticMarkup(
      <DatasetsSection
        api={api}
        datasets={MOCK_DATASETS}
        loading={false}
        onUploaded={() => {}}
        onDeleted={() => {}}
        onRunWithDataset={() => {}}
      />,
    )
    expect(datasetsHtml).toContain('memory.jsonl')
    expect(datasetsHtml).toContain('Preview')

    const cliHtml = renderToStaticMarkup(<CliReferenceSection capabilities={MOCK_OVERVIEW.capabilities} />)
    expect(cliHtml).toContain('ep-branch/neondb')
    expect(cliHtml).toContain('uv run python -m fleet_rlm.optimize')
  })
})
