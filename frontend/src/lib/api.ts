import type { FleetMessage, WorkspaceFileEntry } from './messages'

export interface Session {
  id: string
  title: string
  model_override: string | null
  daytona_sandbox_id: string | null
  created_at: string
  updated_at: string
}

export interface SessionUpdate {
  title?: string
  model_override?: string | null
}

export interface WorkspaceFile extends WorkspaceFileEntry {
  id: string
  session_id: string
  created_at: string
}

export interface Memory {
  id: string
  session_id: string | null
  content: string
  approved: boolean
  created_at: string
}

export type MemoryEdit = { content: string; approved?: boolean } | { approved: boolean }

export interface Skill {
  name: string
  description: string
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
  daytona_target: string | null
  daytona_auto_stop_minutes: number
  daytona_exec_timeout_seconds: number
}

export interface WorkspaceSettingsStatus {
  database_configured: boolean
  daytona_configured: boolean
  daytona_volume_id: string
  daytona_snapshot: string
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
export const sessionPath = (id: string) => `${sessionsPath}/${encodeURIComponent(id)}`
export const chatPath = (id: string) => `${sessionPath(id)}/chat`

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

  return {
    health: (signal?: AbortSignal) => json<{ status: 'ok' }>('/healthz', { signal }, false),
    readiness: (signal?: AbortSignal) => json<{ status: 'ok' }>('/readyz', { signal }, false),
    listSessions: (signal?: AbortSignal) => json<Session[]>(sessionsPath, { signal }),
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
    uploadFile(id: string, file: File) {
      const body = new FormData()
      body.append('file', file)
      return json<WorkspaceFile>(`${sessionPath(id)}/files`, { method: 'POST', body })
    },
    async downloadFile(id: string, path: string) {
      const encoded = path.split('/').map(encodeURIComponent).join('/')
      const response = await request(`${sessionPath(id)}/files/${encoded}`)
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = path.split('/').at(-1) ?? 'download'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
    listMemories(filters: { session_id?: string; approved?: boolean } = {}, signal?: AbortSignal) {
      const params = new URLSearchParams()
      if (filters.session_id) params.set('session_id', filters.session_id)
      if (filters.approved !== undefined) params.set('approved', String(filters.approved))
      return json<Memory[]>(`/api/memories${params.size ? `?${params}` : ''}`, { signal })
    },
    editMemory: (id: string, changes: MemoryEdit) =>
      json<Memory>(`/api/memories/${encodeURIComponent(id)}`, writeJson('PATCH', changes)),
    async deleteMemory(id: string) {
      await request(`/api/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })
    },
    listSkills: (signal?: AbortSignal) => json<Skill[]>('/api/skills', { signal }),
    getSettings: (signal?: AbortSignal) => json<WorkspaceSettingsView>(settingsPath, { signal }),
    updateSettings: (changes: Partial<WorkspaceSettingsFields>) =>
      json<WorkspaceSettingsView>(settingsPath, writeJson('PATCH', changes)),
    resetSettings: () => json<WorkspaceSettingsView>(settingsPath, { method: 'DELETE' }),
  }
}

export type FleetApi = ReturnType<typeof createApi>

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Request failed'
}
