import type { FleetMessage, WorkspaceFileEntry } from './messages'

export interface Session {
  id: string
  title: string
  model_override: string | null
  daytona_sandbox_id: string | null
  deleting: boolean
  created_at: string
  updated_at: string
}

export interface SessionUpdate {
  title?: string
  model_override?: string | null
}

export interface WorkspaceFile extends WorkspaceFileEntry {
  sha256: string
  id: string
  session_id: string
  created_at: string
}

export interface Memory {
  id: string
  session_id: string | null
  source_run_id: string | null
  skill_name: string | null
  content: string
  approved: boolean
  created_at: string
}

export type MemoryEdit = { content: string; approved?: boolean } | { approved: boolean }

export interface Skill {
  name: string
  description: string
}

export interface SkillDetail extends Skill {
  body: string
}

export interface CatalogModel {
  id: string
  label: string
}

export interface ModelCatalog {
  workspace_default: string
  catalog_status: 'ready' | 'unavailable' | 'unconfigured'
  models: CatalogModel[]
}

export interface WorkspaceSettingsFields {
  llm_model: string
  sub_llm_model: string
  decision_model: string
  rlm_max_iters: number
  rlm_max_llm_calls: number
  rlm_max_depth: number
  rlm_max_output_chars: number
  history_max_turns: number
  history_max_chars: number
  daytona_exec_timeout_seconds: number
}

export interface WorkspaceSettingsStatus {
  database_configured: boolean
  daytona_configured: boolean
  daytona_image: string
  daytona_region: string | null
  daytona_idle_timeout_minutes: number
  daytona_max_lifetime_minutes: number
  storage_bucket: string
  llm_api_key_configured: boolean
  llm_base_url: string | null
  bearer_auth_enabled: boolean
  max_concurrent_turns_per_worker: number
  mlflow_tracking_uri: string | null
  mlflow_experiment: string
  optimized_program_path: string | null
  skill_selector_path: string | null
  memory_gate_path: string | null
  skills_count: number
}

export interface WorkspaceSettingsView {
  effective: WorkspaceSettingsFields
  defaults: WorkspaceSettingsFields
  overrides: Partial<WorkspaceSettingsFields>
  status: WorkspaceSettingsStatus
}

export type OptimizationTarget = 'skills' | 'memory' | 'rlm'
export type OptimizationBranchMode = 'auto_neon' | 'configured_branch' | 'custom_branch' | 'direct_readonly'
export type OptimizationJobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type OptimizationStorageBackend = 'builtin' | 'neon' | 'daytona_volume'

export type LLMProviderDialect =
  | 'openai'
  | 'openai_compatible'
  | 'anthropic'
  | 'gemini'
  | 'openrouter'
  | 'xai'
  | 'groq'
  | 'deepseek'
  | 'ollama'
  | 'vllm'
  | 'typesafe'

export type LLMProviderApiMode = 'auto' | 'chat' | 'responses'

export interface LLMProviderModelItem {
  id: string
  label?: string
  owned_by?: string
}

export interface LLMProviderView {
  id: string
  name: string
  dialect: LLMProviderDialect
  api_mode: LLMProviderApiMode
  base_url: string | null
  has_api_key: boolean
  api_key_hint: string | null
  default_model: string | null
  cached_models: LLMProviderModelItem[]
  last_tested_at: string | null
  created_at: string
  updated_at: string
}

export interface LLMProviderListResponse {
  providers: LLMProviderView[]
  encryption_configured: boolean
  allow_private_endpoints: boolean
}

export interface LLMProviderCreateRequest {
  name: string
  dialect: LLMProviderDialect
  api_mode?: LLMProviderApiMode
  base_url?: string | null
  api_key?: string | null
  default_model?: string | null
}

export interface LLMProviderUpdateRequest {
  name?: string
  dialect?: LLMProviderDialect
  api_mode?: LLMProviderApiMode
  base_url?: string | null
  api_key?: string | null
  default_model?: string | null
}

export interface LLMProviderProbeRequest {
  provider_id?: string | null
  dialect: LLMProviderDialect
  api_mode?: LLMProviderApiMode
  base_url?: string | null
  api_key?: string | null
}

export interface LLMProviderProbeResponse {
  ok: boolean
  models: LLMProviderModelItem[]
  count: number
  error?: string | null
}

export interface LLMProviderRefreshModelsResponse {
  provider: LLMProviderView
  models: LLMProviderModelItem[]
  count: number
}

export interface LMRoleSelection {
  reasoning_effort?: 'low' | 'medium' | 'high' | 'xhigh'
  provider_id?: string | null
  model?: string | null
}

export interface OptimizationPromotionView {
  id: string
  target: OptimizationTarget
  job_id: string
  artifact_object_key: string
  artifact_sha256: string
  artifact_size_bytes: number
  resolved_decision_model: string | null
  decision_provider_id?: string | null
  decision_provider_name?: string | null
  program_kind?: string | null
  optimized_score?: number | null
  baseline_score?: number | null
  active: boolean
  promoted_at: string
  rolled_back_at: string | null
}

export interface OptimizationStageTimelineEntry {
  stage: string
  label?: string
  status?: string
  started_at?: string | null
  finished_at?: string | null
  duration_seconds?: number | null
}

export interface OptimizationJobProgress {
  stage?: string
  label?: string
  status?: string
  completed?: number | null
  total?: number | null
  baseline_score?: number | null
  best_score?: number | null
  message?: string | null
  last_active_stage?: string | null
  stage_timeline?: OptimizationStageTimelineEntry[]
  ts?: string
}

export interface OptimizationJobSummary {
  id: string
  target: OptimizationTarget
  program_kind: string | null
  status: OptimizationJobStatus
  stage: string
  dataset_source: 'builtin' | 'custom' | 'db'
  dataset_name: string | null
  dataset_id: string | null
  config_json: Record<string, unknown>
  progress_json: OptimizationJobProgress
  saved: boolean | null
  baseline_score: number | null
  optimized_score: number | null
  train_size: number | null
  val_size: number | null
  holdout_size: number | null
  artifact_object_key: string | null
  artifact_sha256: string | null
  artifact_size_bytes: number | null
  resolved_decision_model: string | null
  decision_provider_id?: string | null
  mlflow_run_id: string | null
  mlflow_experiment: string | null
  mlflow_run_url: string | null
  cancel_requested: boolean
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface OptimizationJobDetail extends OptimizationJobSummary {
  report_json: Record<string, unknown> | null
  log_tail: string
}

export interface TargetStatusSummary {
  target: OptimizationTarget
  source: 'promoted' | 'env' | 'default'
  env_path: string | null
  active_promotion: OptimizationPromotionView | null
  previous_promotion?: OptimizationPromotionView | null
  can_rollback_previous: boolean
  can_rollback_env: boolean
  latest_scored_job?: OptimizationJobSummary | null
  latest_job: OptimizationJobSummary | null
  active_job: OptimizationJobSummary | null
}

export interface OptimizationCapabilities {
  auto_neon_enabled: boolean
  configured_branch_enabled: boolean
  configured_branch_label: string | null
  mlflow_tracking_uri: string | null
  mlflow_experiment: string
  daytona_configured: boolean
  neon_storage_enabled?: boolean
  daytona_volume_enabled?: boolean
  daytona_dataset_volume?: string
  credentials_encryption_enabled?: boolean
  allow_private_llm_endpoints?: boolean
  allow_local_interpreter?: boolean
  decision_model: string
  llm_model: string
  sub_llm_model: string
}

export interface OptimizationOverview {
  targets: TargetStatusSummary[]
  capabilities: OptimizationCapabilities
}

export interface OptimizationDatasetSummary {
  id: string
  name: string
  target: OptimizationTarget
  source: 'builtin' | 'custom'
  storage_backend?: OptimizationStorageBackend
  storage_volume?: string | null
  row_count: number
  size_bytes: number
  sha256: string | null
  summary_json: {
    deletion_pending?: boolean
    row_count?: number
    splits?: Record<string, number>
    lineage_count?: number
    positive_count?: number
    negative_count?: number
    blocking_count?: number
  }
  created_at: string | null
}

export interface OptimizationDatasetPreview {
  dataset: OptimizationDatasetSummary
  rows: Record<string, unknown>[]
}

export interface OptimizationJobCreate {
  target: OptimizationTarget
  dataset_id?: string | null
  program_kind?: 'rlm' | 'flex'
  branch_mode?: OptimizationBranchMode | null
  custom_database_url?: string | null
  allow_direct_opt_in?: boolean
  train_ratio?: number | null
  val_ratio?: number | null
  seed?: number
  auto?: 'light' | 'medium' | 'heavy' | null
  max_metric_calls?: number | null
  holdout_repeats?: number
  disable_cache?: boolean
  local_interpreter?: boolean
  num_threads?: number | null
  limit?: number
  force?: boolean
  reflection_lm?: string | null
  judge_lm?: string | null
  root_lm?: LMRoleSelection | null
  sub_lm?: LMRoleSelection | null
  reflection_lm_config?: LMRoleSelection | null
  judge_lm_config?: LMRoleSelection | null
  decision_lm?: LMRoleSelection | null
}

export interface PromoteJobRequest {
  confirm_non_qualifying?: boolean
  pin_decision_model?: boolean
}

export interface RollbackTargetRequest {
  mode?: 'previous' | 'env'
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function bearerHeaders(token?: string) {
  return token ? { Authorization: `Bearer ${token}` } : undefined
}

export const sessionsPath = '/api/sessions'
export const settingsPath = '/api/settings'
export const optimizationsPath = '/api/optimizations'
export const sessionPath = (id: string) => `${sessionsPath}/${encodeURIComponent(id)}`
export const chatPath = (id: string) => `${sessionPath(id)}/chat`
export const optimizationJobEventsUrl = (id: string) => `${optimizationsPath}/jobs/${encodeURIComponent(id)}/events`

export async function responseError(response: Response): Promise<ApiError> {
  const body = await response.text()
  let message = response.statusText || `HTTP ${response.status}`
  if (response.headers.get('content-type')?.includes('application/json')) {
    try {
      const { detail } = JSON.parse(body) ?? {}
      if (typeof detail === 'string') message = detail
      else if (Array.isArray(detail)) {
        const errors = detail.flatMap((item) => typeof item?.msg === 'string' ? [item.msg] : [])
        if (errors.length) message = errors.join('. ')
      }
    } catch {
      // Fall back to the HTTP status if the response does not contain JSON.
    }
  }
  return new ApiError(response.status, message)
}

export function createApi(token?: string) {
  const auth = bearerHeaders(token)

  async function request(path: string, init?: RequestInit, authenticated = true) {
    const response = await fetch(path, {
      ...init,
      headers: { ...(authenticated ? auth : undefined), ...init?.headers },
    })
    if (!response.ok) throw await responseError(response)
    return response
  }

  async function json<T>(path: string, init?: RequestInit, authenticated = true): Promise<T> {
    return (await request(path, init, authenticated)).json()
  }

  function writeJson(method: 'POST' | 'PATCH', body: object): RequestInit {
    return { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }
  }

  async function getFile(id: string, path: string, signal?: AbortSignal) {
    const encoded = path.split('/').map(encodeURIComponent).join('/')
    const registered = `${sessionPath(id)}/files/${encoded}`
    if (path.startsWith('artifacts/')) return request(registered, { signal })
    if (path.startsWith('files/')) {
      try {
        return await request(registered, { signal })
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error
      }
    }
    return request(`${sessionPath(id)}/workspace/${encoded}`, { signal })
  }

  return {
    health: (signal?: AbortSignal) => json<{ status: 'ok' }>('/healthz', { signal }, false),
    readiness: (signal?: AbortSignal) => json<{ status: 'ok' }>('/readyz', { signal }, false),
    listSessions: (signal?: AbortSignal) => json<Session[]>(sessionsPath, { signal }),
    getSession: (id: string, signal?: AbortSignal) => json<Session>(sessionPath(id), { signal }),
    createSession: (title?: string) => json<Session>(sessionsPath, writeJson('POST', { title })),
    updateSession: (id: string, changes: SessionUpdate) =>
      json<Session>(sessionPath(id), writeJson('PATCH', changes)),
    async deleteSession(id: string) {
      await request(sessionPath(id), { method: 'DELETE' })
    },
    getMessages: (id: string, signal?: AbortSignal) =>
      json<FleetMessage[]>(`${sessionPath(id)}/messages`, { signal }),
    listFiles: (id: string, signal?: AbortSignal) =>
      json<WorkspaceFile[]>(`${sessionPath(id)}/files`, { signal }),
    listWorkspace: (id: string, signal?: AbortSignal) =>
      json<Omit<WorkspaceFileEntry, 'kind' | 'sha256'>[]>(`${sessionPath(id)}/workspace`, { signal }),
    uploadFile(id: string, file: File, signal?: AbortSignal) {
      const body = new FormData()
      body.append('file', file)
      return json<WorkspaceFile>(`${sessionPath(id)}/files`, { method: 'POST', body, signal })
    },
    getFile,
    async downloadFile(id: string, path: string) {
      const response = await getFile(id, path)
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = path.split('/').at(-1) ?? 'download'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
    listMemories(filters: { session_id?: string; memory_id?: string; approved?: boolean; limit?: number } = {}, signal?: AbortSignal) {
      const params = new URLSearchParams()
      if (filters.session_id) params.set('session_id', filters.session_id)
      if (filters.memory_id) params.set('memory_id', filters.memory_id)
      if (filters.approved !== undefined) params.set('approved', String(filters.approved))
      if (filters.limit !== undefined) params.set('limit', String(filters.limit))
      return json<Memory[]>(`/api/memories${params.size ? `?${params}` : ''}`, { signal })
    },
    editMemory: (id: string, changes: MemoryEdit) =>
      json<Memory>(`/api/memories/${encodeURIComponent(id)}`, writeJson('PATCH', changes)),
    async deleteMemory(id: string) {
      await request(`/api/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })
    },
    async refineTurn(sessionId: string, runId: string) {
      const result = await json<{ memory: Memory | null }>(
        `${sessionPath(sessionId)}/runs/${encodeURIComponent(runId)}/refine`, { method: 'POST' },
      )
      return result.memory
    },
    listSkills: (signal?: AbortSignal) => json<Skill[]>('/api/skills', { signal }),
    getSkill: (name: string, signal?: AbortSignal) =>
      json<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`, { signal }),
    installSkill: (id: string, path: string, sha256: string) =>
      json<Skill>(`${sessionPath(id)}/skills`, writeJson('POST', { path, sha256 })),
    listModels: (signal?: AbortSignal) => json<ModelCatalog>('/api/models', { signal }),
    getSettings: (signal?: AbortSignal) => json<WorkspaceSettingsView>(settingsPath, { signal }),
    updateSettings: (changes: Partial<WorkspaceSettingsFields>) =>
      json<WorkspaceSettingsView>(settingsPath, writeJson('PATCH', changes)),
    resetSettings: () => json<WorkspaceSettingsView>(settingsPath, { method: 'DELETE' }),
    getOptimizationOverview: (signal?: AbortSignal) =>
      json<OptimizationOverview>(`${optimizationsPath}/overview`, { signal }),
    listOptimizationDatasets: (signal?: AbortSignal) =>
      json<OptimizationDatasetSummary[]>(`${optimizationsPath}/datasets`, { signal }),
    previewOptimizationDataset: (id: string, limit = 25, signal?: AbortSignal) =>
      json<OptimizationDatasetPreview>(
        `${optimizationsPath}/datasets/${encodeURIComponent(id)}/preview?limit=${limit}`,
        { signal },
      ),
    uploadOptimizationDataset: (body: FormData, signal?: AbortSignal) =>
      json<OptimizationDatasetSummary>(`${optimizationsPath}/datasets`, { method: 'POST', body, signal }),
    async deleteOptimizationDataset(id: string) {
      await request(`${optimizationsPath}/datasets/${encodeURIComponent(id)}`, { method: 'DELETE' })
    },
    listOptimizationJobs(filters: { target?: OptimizationTarget; status?: OptimizationJobStatus; limit?: number } = {}, signal?: AbortSignal) {
      const params = new URLSearchParams()
      if (filters.target) params.set('target', filters.target)
      if (filters.status) params.set('status', filters.status)
      if (filters.limit !== undefined) params.set('limit', String(filters.limit))
      return json<OptimizationJobSummary[]>(
        `${optimizationsPath}/jobs${params.size ? `?${params}` : ''}`,
        { signal },
      )
    },
    getOptimizationJob: (id: string, signal?: AbortSignal) =>
      json<OptimizationJobDetail>(`${optimizationsPath}/jobs/${encodeURIComponent(id)}`, { signal }),
    createOptimizationJob: (payload: OptimizationJobCreate) =>
      json<OptimizationJobDetail>(`${optimizationsPath}/jobs`, writeJson('POST', payload)),
    cancelOptimizationJob: (id: string) =>
      json<OptimizationJobDetail>(`${optimizationsPath}/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    promoteOptimizationJob: (id: string, payload: PromoteJobRequest = {}) =>
      json<OptimizationPromotionView>(
        `${optimizationsPath}/jobs/${encodeURIComponent(id)}/promote`,
        writeJson('POST', payload),
      ),
    rollbackOptimizationTarget: (target: OptimizationTarget, payload: RollbackTargetRequest = { mode: 'previous' }) =>
      json<TargetStatusSummary>(
        `${optimizationsPath}/targets/${encodeURIComponent(target)}/rollback`,
        writeJson('POST', payload),
      ),
    openOptimizationEvents: (id: string, signal?: AbortSignal) =>
      request(optimizationJobEventsUrl(id), {
        headers: { Accept: 'text/event-stream' },
        signal,
      }),
    listOptimizationProviders: (signal?: AbortSignal) =>
      json<LLMProviderListResponse>(`${optimizationsPath}/providers`, { signal }),
    createOptimizationProvider: (payload: LLMProviderCreateRequest) =>
      json<LLMProviderView>(`${optimizationsPath}/providers`, writeJson('POST', payload)),
    updateOptimizationProvider: (id: string, payload: LLMProviderUpdateRequest) =>
      json<LLMProviderView>(
        `${optimizationsPath}/providers/${encodeURIComponent(id)}`,
        writeJson('PATCH', payload),
      ),
    async deleteOptimizationProvider(id: string) {
      await request(`${optimizationsPath}/providers/${encodeURIComponent(id)}`, { method: 'DELETE' })
    },
    probeOptimizationProvider: (payload: LLMProviderProbeRequest) =>
      json<LLMProviderProbeResponse>(`${optimizationsPath}/providers/probe`, writeJson('POST', payload)),
    refreshOptimizationProviderModels: (id: string) =>
      json<LLMProviderRefreshModelsResponse>(
        `${optimizationsPath}/providers/${encodeURIComponent(id)}/models`,
        { method: 'POST' },
      ),
  }
}

export type FleetApi = ReturnType<typeof createApi>

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Request failed'
}
