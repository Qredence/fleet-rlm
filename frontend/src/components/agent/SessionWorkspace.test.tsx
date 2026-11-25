import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SidebarProvider } from '@/components/ui/sidebar'
import type { FleetApi, Session, Skill, WorkspaceFile } from '@/lib/api'
import type { ChatSnapshot, createStreamingChat } from '@/lib/chat-stream'
import type { FleetMessage } from '@/lib/messages'
import { SessionWorkspace } from './SessionWorkspace'

type ChatOptions = Parameters<typeof createStreamingChat>[0]
type FakeChat = ChatSnapshot & {
  subscribe: (listener: (snapshot: ChatSnapshot) => void) => () => void
  emit: (changes: Partial<ChatSnapshot>) => void
  sendMessage: ReturnType<typeof vi.fn>
  stop: ReturnType<typeof vi.fn>
}

const stream = vi.hoisted(() => ({ createStreamingChat: vi.fn() }))
vi.mock('@/lib/chat-stream', async () => {
  const { useLayoutEffect } = await import('react')
  return {
    createStreamingChat: stream.createStreamingChat,
    ChatObserver({ chat, onChange }: { chat: FakeChat; onChange: (snapshot: ChatSnapshot) => void }) {
      useLayoutEffect(() => chat.subscribe(onChange), [chat, onChange])
      return null
    },
  }
})

function fakeChat(messages: FleetMessage[]): FakeChat {
  const listeners = new Set<(snapshot: ChatSnapshot) => void>()
  const chat: FakeChat = {
    messages,
    status: 'ready',
    error: undefined,
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    emit(changes) {
      Object.assign(chat, changes)
      for (const listener of listeners) listener({ messages: chat.messages, status: chat.status, error: chat.error })
    },
    sendMessage: vi.fn(({ text }: { text: string }) => {
      chat.messages = [...chat.messages, { id: 'u2', role: 'user', parts: [{ type: 'text', text }] }]
      chat.status = 'submitted'
      return new Promise(() => {})
    }),
    stop: vi.fn(async () => {}),
  }
  return chat
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
const skills: Skill[] = [{ name: 'pdf-reader', description: 'Read PDF files' }]
const transcript: FleetMessage[] = [
  { id: 'u1', role: 'user', parts: [{ type: 'text', text: 'Summarize the report' }] },
  { id: 'a1', role: 'assistant', parts: [{ type: 'text', text: 'The report covers Q3.' }] },
]

function uploadedFile(name: string): WorkspaceFile {
  return {
    id: name, session_id: 's1', path: `files/${name}`, kind: 'input', sha256: 'abc', size_bytes: 3,
    created_at: '2026-11-01T00:00:00Z',
  }
}

function fakeApi(overrides: Partial<FleetApi> = {}) {
  return {
    getMessages: vi.fn(async () => transcript),
    listFiles: vi.fn(async () => []),
    listWorkspace: vi.fn(async () => []),
    uploadFile: vi.fn(async (_id: string, file: File) => uploadedFile(file.name)),
    downloadFile: vi.fn(async () => {}),
    refineTurn: vi.fn(async () => null),
    ...overrides,
  } as unknown as FleetApi & Record<'getMessages' | 'uploadFile', ReturnType<typeof vi.fn>>
}

function renderWorkspace(api = fakeApi()) {
  const user = userEvent.setup()
  const props = {
    onActivity: vi.fn(), onTurnComplete: vi.fn(), onFilesUploaded: vi.fn(), onSkillInstalled: vi.fn(),
  }
  render(
    <MemoryRouter>
      <SidebarProvider>
        <SessionWorkspace
          api={api} token="secret" session={session} skills={skills} skillsError="" visible
          recentStops={{ current: new Map() }} filesDocked={false} setFilesDocked={vi.fn()}
          onModelChange={vi.fn(async () => session)} {...props}
        />
      </SidebarProvider>
    </MemoryRouter>,
  )
  return { user, api, ...props }
}

const composer = () => screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Fleet' })
const sendButton = () => screen.getByRole<HTMLButtonElement>('button', { name: 'Send' })

describe('SessionWorkspace', () => {
  let chat: FakeChat

  beforeEach(() => {
    stream.createStreamingChat.mockImplementation((options: ChatOptions) => {
      chat = fakeChat(options.messages)
      return chat
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders the saved transcript', async () => {
    renderWorkspace()

    const conversation = await screen.findByLabelText('Conversation')
    const [prompt, answer] = await within(conversation).findAllByRole('article')
    expect(prompt.getAttribute('aria-label')).toBe('You')
    expect(within(prompt).getByText('Summarize the report')).toBeTruthy()
    expect(answer.getAttribute('aria-label')).toBe('Fleet')
    expect(await within(answer).findByText('The report covers Q3.')).toBeTruthy()
  })

  it('shows the empty state with the skill hint', async () => {
    renderWorkspace(fakeApi({ getMessages: vi.fn(async () => []) }))

    expect(await screen.findByText('Start a conversation')).toBeTruthy()
    expect(screen.getByText('Type @ to pin a skill.', { exact: false })).toBeTruthy()
  })

  it('retries a failed transcript load', async () => {
    const getMessages = vi.fn()
      .mockRejectedValueOnce(new Error('Database unavailable'))
      .mockResolvedValueOnce(transcript)
    const { user } = renderWorkspace(fakeApi({ getMessages }))

    expect(await screen.findByText('Database unavailable')).toBeTruthy()
    expect(sendButton().disabled).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Summarize the report')).toBeTruthy()
    expect(getMessages).toHaveBeenCalledTimes(2)
  })

  it('sends a prompt, shows run progress, and stops the run', async () => {
    const { user, onActivity, onTurnComplete, api } = renderWorkspace()
    await screen.findByText('Summarize the report')

    await user.type(composer(), 'Find the totals{Enter}')

    await waitFor(() => expect(chat.sendMessage).toHaveBeenCalledWith({ text: 'Find the totals' }, { body: { pinned_skills: [] } }))
    expect(composer().value).toBe('')
    expect(screen.getByText('Starting run…')).toBeTruthy()
    expect(onActivity).toHaveBeenCalledWith('s1')

    chat.emit({
      status: 'streaming',
      messages: [...chat.messages, { id: 'a2', role: 'assistant', parts: [{ type: 'text', text: 'Totals are 42.' }] }],
    })
    expect(await screen.findByText('Totals are 42.')).toBeTruthy()
    expect(screen.getByText('Run in progress…')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(chat.stop).toHaveBeenCalledOnce()

    chat.emit({ status: 'ready' })
    stream.createStreamingChat.mock.calls[0][0].onComplete()
    await waitFor(() => expect(screen.queryByText('Run in progress…')).toBeNull())
    expect(onActivity).toHaveBeenLastCalledWith(null)
    expect(onTurnComplete).toHaveBeenCalledOnce()
    expect(api.listFiles).toHaveBeenCalledTimes(2)
  })

  it('sends pinned skills with the prompt', async () => {
    const { user } = renderWorkspace()
    await screen.findByText('Summarize the report')

    await user.type(composer(), 'Use @pdf')
    await user.keyboard('{Enter}')
    await user.keyboard('now{Enter}')

    await waitFor(() => expect(chat.sendMessage).toHaveBeenCalledWith(
      { text: 'Use @pdf-reader now' }, { body: { pinned_skills: ['pdf-reader'] } },
    ))
  })

  it('ignores uploads while a turn is active', async () => {
    const { user, api } = renderWorkspace()
    await screen.findByText('Summarize the report')

    await user.type(composer(), 'Hello{Enter}')
    await waitFor(() => expect(chat.sendMessage).toHaveBeenCalled())
    await user.upload(screen.getByLabelText('Attach workspace files'), new File(['a'], 'a.csv'))

    expect(api.uploadFile).not.toHaveBeenCalled()
  })

  it('blocks sending until an upload finishes', async () => {
    let finishUpload!: (file: WorkspaceFile) => void
    const uploadFile = vi.fn(() => new Promise<WorkspaceFile>((resolve) => {
      finishUpload = resolve
    }))
    const { user, onFilesUploaded } = renderWorkspace(fakeApi({ uploadFile }))
    await screen.findByText('Summarize the report')

    await user.upload(screen.getByLabelText('Attach workspace files'), new File(['a'], 'a.csv'))
    await user.type(composer(), 'Read the file{Enter}')

    expect(uploadFile).toHaveBeenCalledOnce()
    expect(sendButton().disabled).toBe(true)
    expect(stream.createStreamingChat).not.toHaveBeenCalled()

    finishUpload(uploadedFile('a.csv'))
    await waitFor(() => expect(sendButton().disabled).toBe(false))
    expect(onFilesUploaded).toHaveBeenCalledOnce()

    await user.click(sendButton())
    await waitFor(() => expect(chat.sendMessage).toHaveBeenCalledWith({ text: 'Read the file' }, expect.anything()))
  })
})
