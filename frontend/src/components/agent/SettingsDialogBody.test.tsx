import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Dialog } from '@/components/ui/dialog'
import { createApi, type Session, type WorkspaceSettingsFields, type WorkspaceSettingsView } from '@/lib/api'
import { SettingsDialogBody } from './SettingsDialogBody'

const defaults: WorkspaceSettingsFields = {
  llm_model: 'openai/gpt-5-mini',
  sub_llm_model: 'openai/gpt-5-nano',
  decision_model: 'typesafe:jev-latest',
  rlm_max_iters: 20,
  rlm_max_llm_calls: 50,
  rlm_max_depth: 4,
  rlm_max_output_chars: 10_000,
  history_max_turns: 8,
  history_max_chars: 16_000,
  daytona_exec_timeout_seconds: 120,
}

function settingsView(overrides: Partial<WorkspaceSettingsFields> = {}): WorkspaceSettingsView {
  return {
    effective: { ...defaults, ...overrides },
    defaults,
    overrides,
    status: {
      database_configured: true,
      daytona_configured: true,
      daytona_image: 'fleet:latest',
      daytona_region: null,
      daytona_idle_timeout_minutes: 15,
      daytona_max_lifetime_minutes: 60,
      storage_bucket: 'fleet-files',
      llm_api_key_configured: true,
      llm_base_url: null,
      bearer_auth_enabled: false,
      max_concurrent_turns_per_worker: 1,
      mlflow_tracking_uri: null,
      mlflow_experiment: 'fleet',
      optimized_program_path: null,
      skill_selector_path: null,
      memory_gate_path: null,
      skills_count: 2,
    },
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

type Route = (body: unknown) => Response

function mockBackend(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    const route = routes[key]
    if (!route) throw new Error(`Unexpected request: ${key}`)
    return route(init?.body ? JSON.parse(String(init.body)) : undefined)
  })
  vi.stubGlobal('fetch', fetchMock)
  const requests = (method: string) => fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? 'GET') === method)
    .map(([input, init]) => ({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined }))
  return { fetchMock, requests }
}

const session: Session = {
  id: 's1',
  title: 'Research',
  model_override: null,
  daytona_sandbox_id: null,
  deleting: false,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
}

function renderSettings({ withSession = false } = {}) {
  const user = userEvent.setup()
  const onUpdateSession = vi.fn(async () => true)
  render(
    <Dialog open>
      <SettingsDialogBody
        api={createApi('secret')} token="secret" onTokenChange={vi.fn()}
        theme="system" systemTheme="light" onThemeChange={vi.fn()}
        session={withSession ? session : undefined} onUpdateSession={onUpdateSession}
      />
    </Dialog>,
  )
  return { user, onUpdateSession }
}

const saveButton = () => screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement
const resetButton = () => screen.getByRole('button', { name: 'Reset defaults' }) as HTMLButtonElement

describe('SettingsDialogBody', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('loads settings and readiness with the bearer token', async () => {
    const { fetchMock } = mockBackend({
      'GET /api/settings': () => json(settingsView()),
      'GET /readyz': () => json({ status: 'ok' }),
    })
    renderSettings()

    expect(await screen.findByText('Ready (/readyz)')).toBeTruthy()
    expect(await screen.findByText('2 skills')).toBeTruthy()
    expect(saveButton().disabled).toBe(true)
    expect(resetButton().disabled).toBe(true)
    const settingsCall = fetchMock.mock.calls.find(([input]) => input === '/api/settings')
    expect(settingsCall?.[1]?.headers).toEqual({ Authorization: 'Bearer secret' })
  })

  it('shows a readiness failure', async () => {
    mockBackend({
      'GET /api/settings': () => json(settingsView()),
      'GET /readyz': () => json({ detail: 'Database unavailable' }, 503),
    })
    renderSettings()

    expect(await screen.findByText('Unavailable')).toBeTruthy()
    expect(screen.getByText('Readiness check failed: Database unavailable')).toBeTruthy()
  })

  it('sends only the changed fields in PATCH /api/settings', async () => {
    const { requests } = mockBackend({
      'GET /api/settings': () => json(settingsView()),
      'GET /readyz': () => json({ status: 'ok' }),
      'PATCH /api/settings': (body) => json(settingsView(body as Partial<WorkspaceSettingsFields>)),
    })
    const { user } = renderSettings()

    await user.click(await screen.findByRole('tab', { name: 'Models' }))
    const model = await screen.findByLabelText('Primary LLM Model')
    await user.clear(model)
    await user.type(model, '  anthropic/claude-sonnet-4  ')
    await user.click(screen.getByRole('tab', { name: 'RLM Limits' }))
    const depth = screen.getByLabelText('Max recursion depth')
    await user.clear(depth)
    await user.type(depth, '6')
    expect(saveButton().disabled).toBe(false)
    await user.click(saveButton())

    expect((await screen.findByRole('status')).textContent).toBe('Settings saved.')
    expect(requests('PATCH')).toEqual([{
      url: '/api/settings',
      body: { llm_model: 'anthropic/claude-sonnet-4', rlm_max_depth: 6 },
    }])
    expect(saveButton().disabled).toBe(true)
    expect(resetButton().disabled).toBe(false)
  })

  it('blocks a blank numeric value without a request', async () => {
    const { requests } = mockBackend({
      'GET /api/settings': () => json(settingsView()),
      'GET /readyz': () => json({ status: 'ok' }),
    })
    const { user } = renderSettings()

    await user.click(await screen.findByRole('tab', { name: 'RLM Limits' }))
    const depth = await screen.findByLabelText('Max recursion depth')
    await user.clear(depth)
    await user.click(saveButton())

    expect(await screen.findByText('Max recursion depth must be an integer between 1 and 7.')).toBeTruthy()
    expect(requests('PATCH')).toEqual([])
  })

  it('shows the backend error when PATCH fails and keeps the draft', async () => {
    mockBackend({
      'GET /api/settings': () => json(settingsView()),
      'GET /readyz': () => json({ status: 'ok' }),
      'PATCH /api/settings': () => json({ detail: [{ msg: 'Unknown model' }] }, 422),
    })
    const { user } = renderSettings()

    await user.click(await screen.findByRole('tab', { name: 'Models' }))
    const model = await screen.findByLabelText<HTMLInputElement>('Sub-LLM Model')
    await user.clear(model)
    await user.type(model, 'bad/model')
    await user.click(saveButton())

    expect(await screen.findByText('Unknown model')).toBeTruthy()
    expect(model.value).toBe('bad/model')
    expect(saveButton().disabled).toBe(false)
  })

  it('resets overrides with DELETE /api/settings', async () => {
    const { requests } = mockBackend({
      'GET /api/settings': () => json(settingsView({ rlm_max_depth: 6 })),
      'GET /readyz': () => json({ status: 'ok' }),
      'DELETE /api/settings': () => json(settingsView()),
    })
    const { user } = renderSettings()

    await user.click(await screen.findByRole('tab', { name: 'RLM Limits' }))
    const depth = await screen.findByLabelText<HTMLInputElement>('Max recursion depth')
    expect(depth.value).toBe('6')
    expect(screen.getByText('Overridden')).toBeTruthy()

    await user.click(resetButton())

    expect((await screen.findByRole('status')).textContent).toBe('Reset workspace settings to .env defaults.')
    expect(requests('DELETE')).toEqual([{ url: '/api/settings', body: undefined }])
    expect(depth.value).toBe('4')
    expect(screen.queryByText('Overridden')).toBeNull()
    expect(resetButton().disabled).toBe(true)
  })

  it('saves only the session model override when workspace settings did not change', async () => {
    const { requests } = mockBackend({
      'GET /api/settings': () => json(settingsView()),
      'GET /readyz': () => json({ status: 'ok' }),
    })
    const { user, onUpdateSession } = renderSettings({ withSession: true })

    await user.click(await screen.findByRole('tab', { name: 'Models' }))
    await user.type(await screen.findByLabelText('Session model_override'), ' openai/gpt-5 ')
    await user.click(saveButton())

    await waitFor(() => expect(onUpdateSession).toHaveBeenCalledWith('s1', { model_override: 'openai/gpt-5' }))
    expect(await screen.findByText('Settings saved.')).toBeTruthy()
    expect(requests('PATCH')).toEqual([])
  })
})
