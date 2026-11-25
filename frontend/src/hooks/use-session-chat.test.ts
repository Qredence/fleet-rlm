import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { STOP_RETRY_WINDOW_MS } from '@/lib/chat-fetch'
import type { createStreamingChat } from '@/lib/chat-stream'
import type { FleetMessage } from '@/lib/messages'
import { useSessionChat } from './use-session-chat'

type ChatOptions = Parameters<typeof createStreamingChat>[0]

const stream = vi.hoisted(() => ({
  createStreamingChat: vi.fn(),
  ChatObserver: () => null,
}))
vi.mock('@/lib/chat-stream', () => stream)

function fakeChat() {
  let finish!: () => void
  let fail!: (error: Error) => void
  const chat = {
    messages: [] as FleetMessage[],
    status: 'ready' as string,
    error: undefined,
    stop: vi.fn(async () => {}),
    sendMessage: vi.fn(() => {
      chat.status = 'submitted'
      return new Promise<void>((resolve, reject) => {
        finish = resolve
        fail = reject
      })
    }),
  }
  return { chat, finish: () => finish(), fail: (error: Error) => fail(error) }
}

const history: FleetMessage[] = [{ id: 'm1', role: 'user', parts: [{ type: 'text', text: 'Earlier' }] }]

function renderChat(initialMessages: FleetMessage[] | null = history) {
  const recentStops = { current: new Map<string, number>() }
  const view = renderHook(() => useSessionChat({ id: 's1', token: 'secret', initialMessages, recentStops, onComplete: vi.fn() }))
  return { ...view, recentStops }
}

describe('useSessionChat', () => {
  let current: ReturnType<typeof fakeChat>

  beforeEach(() => {
    current = fakeChat()
    stream.createStreamingChat.mockImplementation(() => current.chat)
  })

  async function startTurn(result: ReturnType<typeof renderChat>['result']) {
    act(() => {
      void result.current.send('Hello', [], vi.fn())
    })
    await waitFor(() => expect(current.chat.sendMessage).toHaveBeenCalled())
  }

  it('returns the saved transcript before the first send', () => {
    const { result } = renderChat()

    expect(result.current.messages).toBe(history)
    expect(result.current.status).toBe('ready')
    expect(result.current.active).toBe(false)
    expect(result.current.isBusy()).toBe(false)
  })

  it('does not send before the transcript loads', async () => {
    const { result } = renderChat(null)
    const onStart = vi.fn()

    await act(() => result.current.send('Hello', [], onStart))

    expect(stream.createStreamingChat).not.toHaveBeenCalled()
    expect(onStart).not.toHaveBeenCalled()
    expect(result.current.messages).toEqual([])
  })

  it('creates the chat on first send and passes pinned skills', async () => {
    const { result } = renderChat()
    const onStart = vi.fn()

    let sending!: Promise<void>
    act(() => {
      sending = result.current.send('Hello', ['pdf-reader'], onStart)
    })
    expect(result.current.preparing).toBe(true)
    expect(result.current.active).toBe(true)

    await waitFor(() => expect(current.chat.sendMessage).toHaveBeenCalled())
    expect(stream.createStreamingChat).toHaveBeenCalledWith(expect.objectContaining<Partial<ChatOptions>>({
      id: 's1', token: 'secret', messages: history,
    }))
    expect(current.chat.sendMessage).toHaveBeenCalledWith({ text: 'Hello' }, { body: { pinned_skills: ['pdf-reader'] } })
    expect(onStart).toHaveBeenCalledOnce()
    expect(result.current.preparing).toBe(false)
    expect(result.current.status).toBe('submitted')
    expect(result.current.isBusy()).toBe(true)

    await act(async () => {
      current.finish()
      await sending
    })
    expect(result.current.isBusy()).toBe(false)
  })

  it('ignores a second send while a turn is pending', async () => {
    const { result } = renderChat()

    act(() => {
      void result.current.send('First', [], vi.fn())
    })
    await waitFor(() => expect(current.chat.sendMessage).toHaveBeenCalledOnce())
    const onStart = vi.fn()
    await act(() => result.current.send('Second', [], onStart))

    expect(current.chat.sendMessage).toHaveBeenCalledOnce()
    expect(onStart).not.toHaveBeenCalled()
  })

  it('cancels a send that is still loading the chat client', async () => {
    const { result } = renderChat()
    const onStart = vi.fn()

    let sending!: Promise<void>
    act(() => {
      sending = result.current.send('Hello', [], onStart)
    })
    act(() => result.current.stop())
    expect(result.current.preparing).toBe(false)
    expect(result.current.isBusy()).toBe(false)

    await act(() => sending)
    expect(stream.createStreamingChat).not.toHaveBeenCalled()
    expect(onStart).not.toHaveBeenCalled()
    expect(result.current.active).toBe(false)
  })

  it('stops a streaming turn and records the stop for retry', async () => {
    const { result, recentStops } = renderChat()

    await startTurn(result)
    act(() => result.current.stop())

    expect(current.chat.stop).toHaveBeenCalledOnce()
    expect(recentStops.current.has('s1')).toBe(true)

    const { consumeRecentStop } = stream.createStreamingChat.mock.calls[0][0] as ChatOptions
    expect(consumeRecentStop()).toBe(true)
    expect(recentStops.current.has('s1')).toBe(false)
    expect(consumeRecentStop()).toBe(false)
  })

  it('ignores a recorded stop that is older than the retry window', async () => {
    const { result, recentStops } = renderChat()

    await startTurn(result)
    recentStops.current.set('s1', Date.now() - STOP_RETRY_WINDOW_MS - 1)

    const { consumeRecentStop } = stream.createStreamingChat.mock.calls[0][0] as ChatOptions
    expect(consumeRecentStop()).toBe(false)
  })

  it('stops the pending turn on unmount', async () => {
    const { result, unmount, recentStops } = renderChat()

    await startTurn(result)
    unmount()

    expect(current.chat.stop).toHaveBeenCalledOnce()
    expect(recentStops.current.has('s1')).toBe(true)
  })

  it('reports a send failure and accepts the next send', async () => {
    const { result } = renderChat()

    let sending!: Promise<void>
    act(() => {
      sending = result.current.send('Hello', [], vi.fn())
    })
    await waitFor(() => expect(current.chat.sendMessage).toHaveBeenCalled())
    await act(async () => {
      current.fail(new Error('Network down'))
      await sending
    })

    expect(result.current.sendError).toBe('Network down')
    expect(result.current.isBusy()).toBe(false)

    act(() => {
      void result.current.send('Again', [], vi.fn())
    })
    expect(result.current.sendError).toBe('')
    await waitFor(() => expect(current.chat.sendMessage).toHaveBeenCalledTimes(2))
    expect(stream.createStreamingChat).toHaveBeenCalledOnce()
  })

  it('keeps the draft when the chat client cannot load', async () => {
    stream.createStreamingChat.mockImplementationOnce(() => {
      throw new Error('chunk failed')
    })
    const { result } = renderChat()
    const onStart = vi.fn()

    await act(() => result.current.send('Hello', [], onStart))

    expect(result.current.sendError).toBe('Could not load chat. Your draft is still here. Try Send again.')
    expect(onStart).not.toHaveBeenCalled()
    expect(result.current.preparing).toBe(false)
  })
})
