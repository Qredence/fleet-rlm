import { MotionConfig } from 'motion/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ModelSelector } from '@/components/agent/ModelSelector'
import { ActiveRunBanner } from '@/components/optimization/ActiveRunBanner'
import { CliReferenceSection } from '@/components/optimization/CliReferenceSection'
import { DatasetsSection } from '@/components/optimization/DatasetsSection'
import { LaunchOptimizationFormBody } from '@/components/optimization/LaunchOptimizationDialog'
import { ProvidersSection } from '@/components/optimization/ProvidersSection'
import { RunFullDiffContent } from '@/components/optimization/RunFullDiffSheet'
import { RunHistorySection } from '@/components/optimization/RunHistorySection'
import { RunInspectorPanel } from '@/components/optimization/RunInspectorPanel'
import { TargetSummaryGrid } from '@/components/optimization/TargetSummaryGrid'
import { Dialog } from '@/components/ui/dialog'
import { SidebarProvider } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import {
  createApi,
  type LLMProviderView,
  type OptimizationDatasetSummary,
  type OptimizationJobDetail,
  type OptimizationOverview,
} from '@/lib/api'
import { OptimizationPage } from './OptimizationPage'

const MOCK_PROVIDERS: LLMProviderView[] = [
  {
    id: 'prov-openai',
    name: 'OpenAI Prod',
    dialect: 'openai',
    api_mode: 'responses',
    base_url: 'https://api.openai.com/v1',
    has_api_key: true,
    api_key_hint: 'sk-...9a4f',
    default_model: 'gpt-5',
    cached_models: [
      { id: 'gpt-5', label: 'GPT-5', owned_by: 'openai' },
      { id: 'gpt-5-mini', label: 'GPT-5 Mini', owned_by: 'openai' },
    ],
    last_tested_at: '2026-11-26T08:30:00Z',
    created_at: '2026-11-25T10:00:00Z',
    updated_at: '2026-11-26T08:30:00Z',
  },
  {
    id: 'prov-ollama',
    name: 'Local Ollama',
    dialect: 'ollama',
    api_mode: 'chat',
    base_url: 'http://localhost:11434/v1',
    has_api_key: false,
    api_key_hint: null,
    default_model: 'qwen2.5-coder:32b',
    cached_models: [{ id: 'qwen2.5-coder:32b', owned_by: 'ollama' }],
    last_tested_at: null,
    created_at: '2026-11-25T11:00:00Z',
    updated_at: '2026-11-25T11:00:00Z',
  },
]

const MOCK_OVERVIEW: OptimizationOverview = {
  targets: [
    {
      target: 'skills',
      source: 'default',
      env_path: null,
      active_promotion: null,
      previous_promotion: null,
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
        id: 'promo-2',
        target: 'memory',
        job_id: 'job-1',
        artifact_object_key: 'optimizations/artifacts/job-1/memory.json',
        artifact_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
        artifact_size_bytes: 1024,
        resolved_decision_model: 'typesafe:jev-1.14.0',
        decision_provider_id: null,
        decision_provider_name: 'Workspace TypeSafe',
        program_kind: null,
        optimized_score: 0.8286,
        baseline_score: 0.7714,
        active: true,
        promoted_at: '2026-11-26T00:00:00Z',
        rolled_back_at: null,
      },
      previous_promotion: {
        id: 'promo-1',
        target: 'memory',
        job_id: 'job-0',
        artifact_object_key: 'optimizations/artifacts/job-0/memory.json',
        artifact_sha256: 'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210',
        artifact_size_bytes: 980,
        resolved_decision_model: 'typesafe:jev-1.13.0',
        decision_provider_id: null,
        decision_provider_name: null,
        program_kind: null,
        optimized_score: 0.79,
        baseline_score: 0.75,
        active: false,
        promoted_at: '2026-11-24T12:00:00Z',
        rolled_back_at: '2026-11-26T00:00:00Z',
      },
      can_rollback_previous: true,
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
        decision_provider_id: null,
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
      previous_promotion: null,
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
    neon_storage_enabled: true,
    daytona_volume_enabled: true,
    daytona_dataset_volume: 'fleet-eval-datasets',
    credentials_encryption_enabled: true,
    allow_private_llm_endpoints: false,
    allow_local_interpreter: false,
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
    storage_backend: 'builtin',
    storage_volume: null,
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
  {
    id: 'ds-daytona-1',
    name: 'customer-rlm-volume.jsonl',
    target: 'rlm',
    source: 'custom',
    storage_backend: 'daytona_volume',
    storage_volume: 'fleet-eval-datasets',
    row_count: 48,
    size_bytes: 28400,
    sha256: 'def456',
    summary_json: {
      row_count: 48,
      splits: { train: 28, val: 8, holdout: 12 },
      lineage_count: 16,
      blocking_count: 4,
    },
    created_at: '2026-11-26T04:00:00Z',
  },
]

const MOCK_REANCHOR_JOB: OptimizationJobDetail = {
  ...MOCK_OVERVIEW.targets[1].latest_job!,
  status: 'completed',
  stage: 'completed',
  progress_json: {
    stage: 'completed',
    label: 'Completed',
    completed: 40,
    total: 40,
    baseline_score: 0.7714,
    best_score: 0.8286,
    stage_timeline: [
      { stage: 'loading_dataset', label: 'Load dataset', status: 'completed', duration_seconds: 1.2 },
      { stage: 'reanchor_cv', label: 'ReAnchor 5-fold CV', status: 'completed', duration_seconds: 18.4 },
      { stage: 'holdout_eval', label: 'Holdout FP/FN eval', status: 'completed', duration_seconds: 4.1 },
    ],
  },
  report_json: {
    target: 'memory',
    eligible: true,
    gate_checks: [
      {
        name: 'parameters_changed',
        label: 'Parameters changed from baseline',
        passed: true,
        actual: 2,
        threshold: '>= 1 fitted field',
        reason: 'Calibrated 2 field(s): worth_saving, action',
      },
      {
        name: 'holdout_score_improved',
        label: 'Holdout score improved over baseline',
        passed: true,
        actual: 0.8286,
        threshold: '> 0.7714',
        reason: 'Holdout score improved from 0.7714 to 0.8286',
      },
      {
        name: 'fp_rate_non_regression',
        label: 'False-positive rate did not regress',
        passed: true,
        actual: 0.05,
        threshold: '<= 0.2000',
        reason: 'FP rate 0.0500 <= baseline 0.2000',
      },
    ],
    fp_rate_before: 0.2,
    fp_rate_after: 0.05,
    diagnostics_before: {
      fp_rate: 0.2,
      fn_rate: 0.14,
      action_accuracy: 0.81,
      scope_accuracy: 0.88,
      auto_approve_fp_count: 3,
    },
    diagnostics_after: {
      fp_rate: 0.05,
      fn_rate: 0.1,
      action_accuracy: 0.9,
      scope_accuracy: 0.95,
      auto_approve_fp_count: 0,
    },
    fitted_parameters: { worth_saving: { threshold: 0.62 } },
    parameter_diffs: [
      { gate: 'worth_saving', parameter: 'threshold', baseline: 0.5, fitted: 0.62, delta: 0.12 },
      { gate: 'action', parameter: 'weight:skip', baseline: 1.0, fitted: 1.35, delta: 0.35 },
    ],
    case_deltas: [
      {
        case_id: 'mem-101',
        lineage: 'session-a',
        input_preview: 'Remember my preferred region is us-east-1',
        expected: 'save',
        baseline_pred: 'skip',
        baseline_prob: 0.48,
        baseline_score: 0.0,
        candidate_pred: 'save',
        candidate_prob: 0.71,
        candidate_score: 1.0,
        delta: 1.0,
        outcome: 'fixed',
      },
    ],
    models: {
      decision_lm: {
        dialect: 'typesafe',
        model: 'typesafe:jev-1.14.0',
        provider_name: 'Workspace TypeSafe',
      },
    },
  },
  log_tail: '[memory] ReAnchor CV completed cleanly.',
}

const MOCK_RUNNING_JOB: OptimizationJobDetail = {
  ...MOCK_REANCHOR_JOB,
  status: 'running',
  stage: 'reanchor_cv',
  progress_json: {
    stage: 'reanchor_cv',
    label: 'Calibrating thresholds & Choice weights via ReAnchor CV',
    completed: 12,
    total: 40,
    baseline_score: 0.7714,
  },
}

const MOCK_RLM_JOB: OptimizationJobDetail = {
  ...MOCK_OVERVIEW.targets[1].latest_job!,
  id: 'job-rlm-1',
  target: 'rlm',
  program_kind: 'rlm',
  status: 'completed',
  stage: 'completed',
  dataset_name: 'customer-rlm-volume.jsonl',
  saved: false,
  baseline_score: 0.72,
  optimized_score: 0.84,
  train_size: 28,
  val_size: 8,
  holdout_size: 12,
  artifact_object_key: null,
  artifact_sha256: null,
  resolved_decision_model: null,
  report_json: {
    target: 'rlm',
    program_kind: 'rlm',
    eligible: false,
    gate_checks: [
      {
        name: 'instructions_changed',
        label: 'Instructions changed from baseline',
        passed: true,
        actual: true,
        threshold: true,
        reason: 'Compiled program modified instructions',
      },
      {
        name: 'zero_blocking_failures',
        label: 'Zero blocking case failures',
        passed: false,
        actual: 1,
        threshold: 0,
        reason: '1 blocking holdout failure(s) detected',
      },
    ],
    metrics: {
      baseline_holdout_score: 0.72,
      optimized_holdout_score: 0.84,
      baseline_correctness: 0.75,
      optimized_correctness: 0.88,
      baseline_submit_rate: 0.9,
      optimized_submit_rate: 0.96,
      baseline_mean_actions: 4.5,
      optimized_mean_actions: 3.2,
      baseline_py_errors: 2,
      optimized_py_errors: 0,
    },
    baseline_instructions: {
      generate_action: 'Write Python to solve the task.',
    },
    optimized_instructions: {
      generate_action: 'Write concise Python to inspect workspace files and verify output before SUBMIT.',
    },
    instruction_diffs: {
      generate_action: [
        '--- baseline/generate_action',
        '+++ optimized/generate_action',
        '@@ -1 +1 @@',
        '-Write Python to solve the task.',
        '+Write concise Python to inspect workspace files and verify output before SUBMIT.',
      ].join('\n'),
    },
    candidate_history: [
      {
        candidate_idx: 0,
        val_score: 72.0,
        parent: null,
        parents: [],
        accepted: true,
        is_best: false,
        metric_calls: 8,
        reflection_summary: 'Baseline seed candidate',
      },
      {
        candidate_idx: 1,
        val_score: 86.5,
        parent: 0,
        parents: [0],
        accepted: true,
        is_best: true,
        metric_calls: 24,
        reflection_summary: 'Mutated generate_action (+1/-1 lines)',
      },
    ],
    case_deltas: [
      {
        case_id: 'rlm-block-7',
        lineage: 'billing-csv',
        input_preview: 'Compute total invoice delta from workspace/invoices.csv',
        blocking: true,
        blocking_failed: true,
        paired_regression: true,
        baseline_score: 1.0,
        candidate_score: 0.0,
        baseline_correctness: 1.0,
        candidate_correctness: 0.0,
        baseline_pass_rate: 1.0,
        candidate_pass_rate: 0.0,
        delta: -1.0,
        repeats: 3,
        outcome: 'regressed',
        baseline_feedback: 'Exact match',
        candidate_feedback: 'Missed currency rounding on row 14',
      },
    ],
    models: {
      root_lm: { dialect: 'openai', model: 'gpt-5', provider_name: 'OpenAI Prod' },
      reflection_lm: { dialect: 'openai', model: 'gpt-5', provider_name: 'OpenAI Prod' },
    },
  },
  log_tail: '[rlm] GEPA finished 24 metric calls.',
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

  it('wires provider CRUD, probe, /models refresh, Daytona Volume dataset upload, and per-role LM job creation', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
      new Response(JSON.stringify({ ok: true, providers: [], models: [], count: 0 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const api = createApi('secret-bearer')
    await api.listOptimizationProviders()
    await api.createOptimizationProvider({
      name: 'OpenAI Prod',
      dialect: 'openai',
      api_mode: 'responses',
      api_key: 'sk-test-1234',
    })
    await api.updateOptimizationProvider('prov-openai', { api_key: null })
    await api.probeOptimizationProvider({
      dialect: 'openai_compatible',
      api_mode: 'chat',
      base_url: 'https://gateway.example.com/v1',
    })
    await api.refreshOptimizationProviderModels('prov-openai')
    await api.deleteOptimizationProvider('prov-openai')

    const form = new FormData()
    form.append('target', 'rlm')
    form.append('storage_backend', 'daytona_volume')
    form.append('file', new Blob(['{"task":"hi"}\n']), 'eval.jsonl')
    await api.uploadOptimizationDataset(form)

    await api.createOptimizationJob({
      target: 'rlm',
      dataset_id: 'ds-daytona-1',
      root_lm: { provider_id: 'prov-openai', model: 'gpt-5' },
      reflection_lm_config: { provider_id: 'prov-openai', model: 'gpt-5' },
      seed: 0,
    })
    await api.promoteOptimizationJob('job-1', {
      confirm_non_qualifying: true,
      pin_decision_model: true,
    })
    await api.rollbackOptimizationTarget('memory', { mode: 'previous' })
    await api.deleteOptimizationDataset('custom-1')

    expect(fetchMock.mock.calls.map((call) => [call[0], call[1]?.method ?? 'GET'])).toEqual([
      ['/api/optimizations/providers', 'GET'],
      ['/api/optimizations/providers', 'POST'],
      ['/api/optimizations/providers/prov-openai', 'PATCH'],
      ['/api/optimizations/providers/probe', 'POST'],
      ['/api/optimizations/providers/prov-openai/models', 'POST'],
      ['/api/optimizations/providers/prov-openai', 'DELETE'],
      ['/api/optimizations/datasets', 'POST'],
      ['/api/optimizations/jobs', 'POST'],
      ['/api/optimizations/jobs/job-1/promote', 'POST'],
      ['/api/optimizations/targets/memory/rollback', 'POST'],
      ['/api/optimizations/datasets/custom-1', 'DELETE'],
    ])

    const uploadBody = fetchMock.mock.calls[6]?.[1]?.body
    expect(uploadBody).toBeInstanceOf(FormData)
    expect((uploadBody as FormData).get('storage_backend')).toBe('daytona_volume')

    const jobBody = JSON.parse(String(fetchMock.mock.calls[7]?.[1]?.body))
    expect(jobBody.root_lm).toEqual({ provider_id: 'prov-openai', model: 'gpt-5' })
    expect(jobBody.reflection_lm_config).toEqual({ provider_id: 'prov-openai', model: 'gpt-5' })
  })
})

describe('Optimization Control Plane SSR rendering & v2 UX components', () => {
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

  it('renders page shell with 4 tabs (Runs, Datasets, Providers, CLI & Environment), process guide, targets, banner, history, and CLI capabilities', () => {
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
    expect(pageHtml).toContain('Providers')
    expect(pageHtml).toContain('CLI &amp; Environment')
    expect(pageHtml).toContain('How optimization works')

    const gridHtml = renderToStaticMarkup(
      <TargetSummaryGrid
        overview={{ ...MOCK_OVERVIEW, targets: MOCK_OVERVIEW.targets.map(target => ({
          ...target,
          latest_scored_job: target.latest_job,
          latest_job: target.latest_job && { ...target.latest_job, status: 'cancelled', optimized_score: null },
        })) }}
        loading={false}
        onLaunchTarget={() => {}}
        onRollbackTarget={async () => {}}
      />,
    )
    expect(gridHtml).toContain('opt-subgrid-cards')
    expect(gridHtml).toContain('Skill Selector')
    expect(gridHtml).toContain('Memory Gate')
    expect(gridHtml).toContain('Workspace TypeSafe')
    expect(gridHtml).toContain('typesafe:jev-1.14.0')
    expect(gridHtml).toContain('82.9%')
    expect(gridHtml).toContain('Rollback')

    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const bannerHtml = renderToStaticMarkup(
      <MotionConfig reducedMotion="user">
        <TooltipProvider>
          <ActiveRunBanner
            job={MOCK_RUNNING_JOB}
            onCancel={async () => {}}
            onInspect={() => {}}
            onDismiss={() => {}}
          />
        </TooltipProvider>
      </MotionConfig>,
    )
    expect(consoleErrorSpy).not.toHaveBeenCalled()
    consoleErrorSpy.mockRestore()
    expect(bannerHtml).toContain('<progress')
    expect(bannerHtml).toContain('opt-progress')
    expect(bannerHtml).toContain('12/40')
    expect(bannerHtml).not.toMatch(/<p\b[^>]*>(?:(?!<\/p>)[\s\S])*<div\b/i)

    const historyHtml = renderToStaticMarkup(
      <TooltipProvider>
        <RunHistorySection
          overview={MOCK_OVERVIEW}
          jobs={[MOCK_OVERVIEW.targets[1].latest_job!]}
          loading={false}
          selectedJobId="job-1"
          selectedJob={MOCK_REANCHOR_JOB}
          loadingSelected={false}
          targetFilter="all"
          statusFilter="all"
          onTargetFilterChange={() => {}}
          onStatusFilterChange={() => {}}
          onSelectJob={() => {}}
          onPromote={async () => {}}
          onRerun={() => {}}
          onOpenFullDiff={() => {}}
          onLaunchFirst={() => {}}
        />
      </TooltipProvider>,
    )
    expect(historyHtml).toContain('opt-table-wrapper')
    expect(historyHtml).toContain('role="region"')
    expect(historyHtml).toContain('data-label="Target"')
    expect(historyHtml).toContain('aria-selected="true"')
    expect(historyHtml).toContain('Completed')
    expect(historyHtml).toContain('Active')

    const cliHtml = renderToStaticMarkup(
      <TooltipProvider>
        <CliReferenceSection capabilities={MOCK_OVERVIEW.capabilities} />
      </TooltipProvider>,
    )
    expect(cliHtml).toContain('ep-branch/neondb')
    expect(cliHtml).toContain('fleet-eval-datasets')
    expect(cliHtml).toContain('Fernet AEAD active')
    expect(cliHtml).toContain('Blocked by default')
    expect(cliHtml).toContain('uv run python -m fleet_rlm.optimize')
  })

  it('shows the short model name in the closed selector', () => {
    const html = renderToStaticMarkup(
      <ModelSelector api={createApi('token')} model="provider/prov-openai/gpt-5"
        disabled={false} saving={false} onChange={() => {}} />,
    )
    expect(html).toContain('Model: GPT-5')
    expect(html).toContain('>GPT-5</span>')
    expect(html).not.toContain('Model ·')
    expect(html).not.toContain('prov-openai')
  })

  it('renders ProvidersSection cards, masked hints, keyless local provider, and encryption warning banner', () => {
    const api = createApi('token')
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <ProvidersSection
          loadError={null}
          onRetry={() => {}}
          api={api}
          providers={MOCK_PROVIDERS}
          encryptionConfigured={false}
          allowPrivateEndpoints={false}
          loading={false}
          onChanged={() => {}}
        />
      </TooltipProvider>,
    )
    expect(html).toContain('FLEET_CREDENTIALS_ENCRYPTION_KEY is not configured')
    expect(html).toContain('Fernet.generate_key()')
    expect(html).toContain('OpenAI Prod')
    expect(html).toContain('sk-...9a4f')
    expect(html).toContain('Responses API')
    expect(html).toContain('Local Ollama')
    expect(html).toContain('No API key')
    expect(html).toContain('Fetch /models')
    expect(html).toContain('provider-card-prov-openai')

    const rows = renderToStaticMarkup(
      <TooltipProvider>
        <ProvidersSection api={api} providers={MOCK_PROVIDERS} layout="rows"
          encryptionConfigured allowPrivateEndpoints={false} loading={false}
          loadError={null} onRetry={() => {}} onChanged={() => {}} onOpenModels={() => {}} />
      </TooltipProvider>,
    )
    expect(rows).toContain('provider-row-prov-openai')
    expect(rows).not.toContain('provider-card-')
    expect(rows).toContain('sk-...9a4f')
    expect(rows).toContain('Fetch /models')
    expect(rows).toContain('Open Models')
    expect(rows).toContain('Edit provider OpenAI Prod')
    expect(rows).toContain('Delete provider OpenAI Prod')
  })

  it('shows provider fetch errors with a retry action instead of an empty registry', () => {
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <ProvidersSection api={createApi('token')} providers={null} loading={false}
          encryptionConfigured allowPrivateEndpoints={false} onChanged={() => {}}
          loadError="Service unavailable" onRetry={() => {}} />
      </TooltipProvider>,
    )
    expect(html).toContain('Service unavailable')
    expect(html).toContain('Retry')
    expect(html).not.toContain('No custom LLM providers registered yet')
  })

  it('renders DatasetsSection with Neon vs Daytona Volume selector, dropzone, and storage badges', () => {
    const api = createApi('token')
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <DatasetsSection
          api={api}
          datasets={MOCK_DATASETS}
          capabilities={MOCK_OVERVIEW.capabilities}
          loading={false}
          onUploaded={() => {}}
          onDeleted={() => {}}
          onRunWithDataset={() => {}}
        />
      </TooltipProvider>,
    )
    expect(html).toContain('Storage backend')
    expect(html).toContain('data-testid="dataset-dropzone"')
    expect(html).toContain('Built-in')
    expect(html).toContain('Daytona Volume')
    expect(html).toContain('customer-rlm-volume.jsonl')
    expect(html).toContain('fleet-eval-datasets')
  })

  it('renders LaunchOptimizationFormBody with per-role model pickers for RLM and ReAnchor calibration notice', () => {
    const api = createApi('token')

    const rlmFormHtml = renderToStaticMarkup(
      <TooltipProvider>
        <Dialog open>
          <LaunchOptimizationFormBody
            api={api}
            capabilities={MOCK_OVERVIEW.capabilities}
            datasets={MOCK_DATASETS}
            providers={MOCK_PROVIDERS}
            defaults={{ target: 'rlm', programKind: 'flex' }}
            onClose={() => {}}
            onStarted={() => {}}
            onManageProviders={() => {}}
          />
        </Dialog>
      </TooltipProvider>,
    )
    expect(rlmFormHtml).toContain('Root LM (agent being optimized)')
    expect(rlmFormHtml).toContain('Reflection LM (GEPA prompt editor)')
    expect(rlmFormHtml).toContain('Manage providers…')
    expect(rlmFormHtml).toContain('evaluation-only')
    expect(rlmFormHtml).not.toContain('Use local DSPy interpreter')

    const nonJevReanchorHtml = renderToStaticMarkup(
      <TooltipProvider>
        <Dialog open>
          <LaunchOptimizationFormBody
            api={api}
            capabilities={MOCK_OVERVIEW.capabilities}
            datasets={MOCK_DATASETS}
            providers={MOCK_PROVIDERS}
            defaults={{
              target: 'skills',
              decisionLm: { provider_id: 'prov-openai', model: 'gpt-5' },
            }}
            onClose={() => {}}
            onStarted={() => {}}
            onManageProviders={() => {}}
          />
        </Dialog>
      </TooltipProvider>,
    )
    expect(nonJevReanchorHtml).toContain('Decision model')
    expect(nonJevReanchorHtml).toContain('Calibration notice')
    expect(nonJevReanchorHtml).toContain('TypeSafe Jev System One')
  })

  it.each([
    ['memory', '0.0%'], ['skills', '0.0%'], ['rlm', '0.00%'],
  ] as const)('renders legacy %s holdout scores in both reports', (target, zero) => {
    const job = {
      ...MOCK_REANCHOR_JOB, target, baseline_score: 0, optimized_score: 1, report_json: {},
      config_json: { models: { decision_lm: { model: 'typesafe:historical-model' } } },
    }
    const inspector = renderToStaticMarkup(
      <TooltipProvider>
        <RunInspectorPanel job={job} loading={false} activePromotionJobId={null}
          onPromote={async () => {}} onRerun={() => {}} onOpenFullDiff={() => {}} />
      </TooltipProvider>,
    )
    const diff = renderToStaticMarkup(
      <TooltipProvider><RunFullDiffContent job={job} initialTab={0} /></TooltipProvider>,
    )
    expect(inspector).toContain('typesafe:historical-model')
    for (const html of [inspector, diff]) {
      expect(html).toContain('Holdout score improved over baseline')
      expect(html).toContain(zero)
      expect(html).toContain(target === 'rlm' ? '1.00%' : '100.0%')
    }
  })

  it('renders RunInspectorPanel executive summary, gate checklist, Flex disabled reason, and RunFullDiffContent tabs for ReAnchor & GEPA', () => {
    const inspectorHtml = renderToStaticMarkup(
      <TooltipProvider>
        <RunInspectorPanel
          job={MOCK_REANCHOR_JOB}
          loading={false}
          activePromotionJobId={null}
          activeTargetSummary={MOCK_OVERVIEW.targets[1]}
          onPromote={async () => {}}
          onRerun={() => {}}
          onOpenFullDiff={() => {}}
        />
      </TooltipProvider>,
    )
    expect(inspectorHtml).toContain('Passed all promotion gates')
    expect(inspectorHtml).toContain('Promotion gate checklist')
    expect(inspectorHtml).toContain('Open Full Diff &amp; Diagnostics')
    expect(inspectorHtml).toContain('Workspace TypeSafe')
    expect(inspectorHtml).toContain('worth_saving')
    expect(inspectorHtml).toContain('MLflow')
    expect(inspectorHtml).toContain('Promote')

    const flexInspectorHtml = renderToStaticMarkup(
      <TooltipProvider>
        <RunInspectorPanel
          job={{ ...MOCK_RLM_JOB, program_kind: 'flex', saved: true }}
          loading={false}
          activePromotionJobId={null}
          onPromote={async () => {}}
          onRerun={() => {}}
          onOpenFullDiff={() => {}}
        />
      </TooltipProvider>,
    )
    expect(flexInspectorHtml).toContain('dspy.Flex runs are evaluation-only')
    expect(flexInspectorHtml).toContain('Gate rejected candidate')

    // Full Diff Sheet Tab 0 (Gates & Timeline)
    const gatesTabHtml = renderToStaticMarkup(
      <TooltipProvider>
        <RunFullDiffContent job={MOCK_REANCHOR_JOB} initialTab={0} />
      </TooltipProvider>,
    )
    expect(gatesTabHtml).toContain('Promotion gate checklist')
    expect(gatesTabHtml).toContain('Parameters changed from baseline')
    expect(gatesTabHtml).toContain('Execution stage timeline')
    expect(gatesTabHtml).toContain('18s')

    // Full Diff Sheet Tab 1 (ReAnchor Parameter Diffs & Calibration Matrix)
    const reanchorDiffHtml = renderToStaticMarkup(
      <TooltipProvider>
        <RunFullDiffContent job={MOCK_REANCHOR_JOB} initialTab={1} />
      </TooltipProvider>,
    )
    expect(reanchorDiffHtml).toContain('ReAnchor parameter changes')
    expect(reanchorDiffHtml).toContain('worth_saving')
    expect(reanchorDiffHtml).toContain('+0.120')
    expect(reanchorDiffHtml).toContain('Before → After calibration matrix')
    expect(reanchorDiffHtml).toContain('Auto-approve false positives')

    // Full Diff Sheet Tab 1 (GEPA Unified Instruction Diff & Evolution Trajectory)
    const gepaDiffHtml = renderToStaticMarkup(
      <TooltipProvider>
        <RunFullDiffContent job={MOCK_RLM_JOB} initialTab={1} />
      </TooltipProvider>,
    )
    const metricCells = (label: string) =>
      gepaDiffHtml.match(new RegExp('>' + label + '</td>(.*?)</tr>'))?.[1]?.replace(/<[^>]+>/g, ' ')
    expect(metricCells('Holdout score')).toMatch(/0\.7%.*0\.8%.*\+0\.1 pts/)
    expect(metricCells('Correctness')).toMatch(/75\.0%.*88\.0%.*\+13\.0 pts/)
    expect(gepaDiffHtml).toContain('Prompt &amp; instruction diffs')
    expect(gepaDiffHtml).toContain('data-variant="removed"')
    expect(gepaDiffHtml).toContain('data-variant="added"')
    expect(gepaDiffHtml).toContain('GEPA evolution trajectory')
    expect(gepaDiffHtml).toContain('Mutated generate_action (+1/-1 lines)')

    // Full Diff Sheet Tab 2 (Per-example Holdout Cases)
    const casesTabHtml = renderToStaticMarkup(
      <TooltipProvider>
        <RunFullDiffContent job={MOCK_RLM_JOB} initialTab={2} />
      </TooltipProvider>,
    )
    expect(casesTabHtml).toContain('rlm-block-7')
    expect(casesTabHtml).toContain('Blocking failure')
    expect(casesTabHtml).toContain('Paired regression')
    expect(casesTabHtml).toContain('Missed currency rounding on row 14')

    // Full Diff Sheet Tab 3 (Logs & Provenance)
    const logsTabHtml = renderToStaticMarkup(
      <TooltipProvider>
        <RunFullDiffContent job={MOCK_RLM_JOB} initialTab={3} />
      </TooltipProvider>,
    )
    expect(logsTabHtml).toContain('OpenAI Prod')
    expect(logsTabHtml).toContain('GEPA finished 24 metric calls.')
  })
})
