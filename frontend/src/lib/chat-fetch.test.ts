import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, responseError } from './api'
import { chatFetch, STOP_RETRY_WINDOW_MS } from './chat-fetch'

function jsonResponse(body: unknown, status: number, statusText = ''): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { 'content-type': 'application/json' },
  })
}

describe('chatFetch', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('window', globalThis)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('exports the stop-retry window constant', () => {
    expect(STOP_RETRY_WINDOW_MS).toBe(10_000)
  })

  it('passes through non-409 successful responses without waiting', async () => {
    const okResponse = new Response('ok', { status: 200 })
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(okResponse)
    vi.stubGlobal('fetch', fetchMock)
    const setWaiting = vi.fn()

    const wrapped = chatFetch(() => true, setWaiting)
    const result = await wrapped('/api/sessions/1/chat', { method: 'POST' })

    expect(result).toBe(okResponse)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(setWaiting).not.toHaveBeenCalled()
  })

  it('passes through non-409 error responses immediately without retrying', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Invalid bearer token' }, 401, 'Unauthorized'))
    vi.stubGlobal('fetch', fetchMock)
    const setWaiting = vi.fn()

    const wrapped = chatFetch(() => true, setWaiting)

    await expect(wrapped('/api/sessions/1/chat', { method: 'POST' })).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      message: 'Invalid bearer token',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(setWaiting).not.toHaveBeenCalled()
  })

  it('retries 409 responses after a recent stop until the turn succeeds', async () => {
    const okResponse = new Response('stream-ready', { status: 200 })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Run in progress' }, 409))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Run in progress' }, 409))
      .mockResolvedValueOnce(okResponse)
    vi.stubGlobal('fetch', fetchMock)
    const setWaiting = vi.fn()

    const onAccepted = vi.fn()
    const wrapped = chatFetch(() => true, setWaiting, onAccepted)
    const pending = wrapped('/api/sessions/1/chat', { method: 'POST' })

    await vi.advanceTimersByTimeAsync(300)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(setWaiting).toHaveBeenCalledWith(true)
    expect(onAccepted).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(600)
    const response = await pending

    expect(response).toBe(okResponse)
    expect(response.bodyUsed).toBe(false)
    expect(onAccepted).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(setWaiting.mock.calls).toEqual([[true], [false]])
  })

  it('throws ApiError after exhausting all 409 stop-retry attempts', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse({ detail: 'A run is already active for this session' }, 409))
    vi.stubGlobal('fetch', fetchMock)
    const setWaiting = vi.fn()

    const wrapped = chatFetch(() => true, setWaiting)
    const pending = wrapped('/api/sessions/1/chat', { method: 'POST' })
    const assertion = expect(pending).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      message: 'A run is already active for this session',
    })

    await vi.advanceTimersByTimeAsync(300 + 600 + 1000 + 1500)
    await assertion

    expect(fetchMock).toHaveBeenCalledTimes(5)
    expect(setWaiting.mock.calls).toEqual([[true], [false]])
  })

  it('does not retry 409 outside the stop-retry window or on non-POST requests', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Conflict' }, 409))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Conflict' }, 409))
    vi.stubGlobal('fetch', fetchMock)
    const setWaiting = vi.fn()

    const noStopWrapped = chatFetch(() => false, setWaiting)
    await expect(noStopWrapped('/api/sessions/1/chat', { method: 'POST' })).rejects.toBeInstanceOf(ApiError)

    const getWrapped = chatFetch(() => true, setWaiting)
    await expect(getWrapped('/api/sessions/1/chat', { method: 'GET' })).rejects.toBeInstanceOf(ApiError)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(setWaiting).not.toHaveBeenCalled()
  })

  it('aborts cleanly during retry backoff and clears waiting state', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: 'Run in progress' }, 409))
    vi.stubGlobal('fetch', fetchMock)
    const setWaiting = vi.fn()
    const controller = new AbortController()
    const abortReason = new DOMException('Request aborted', 'AbortError')

    const wrapped = chatFetch(() => true, setWaiting)
    const pending = wrapped('/api/sessions/1/chat', {
      method: 'POST',
      signal: controller.signal,
    })
    const assertion = expect(pending).rejects.toBe(abortReason)

    await vi.advanceTimersByTimeAsync(100)
    controller.abort(abortReason)
    await assertion

    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(setWaiting.mock.calls).toEqual([[true], [false]])
  })

  it('parses FastAPI validation error arrays and falls back for non-JSON errors', async () => {
    const validationErr = await responseError(
      jsonResponse(
        {
          detail: [
            { msg: 'Input should be less than or equal to 7' },
            { msg: 'Extra inputs are not permitted' },
          ],
        },
        422,
        'Unprocessable Content',
      ),
    )
    expect(validationErr).toBeInstanceOf(ApiError)
    expect(validationErr.status).toBe(422)
    expect(validationErr.message).toBe('Input should be less than or equal to 7. Extra inputs are not permitted')

    const plainTextErr = await responseError(
      new Response('Bad gateway', { status: 502, statusText: 'Bad Gateway' }),
    )
    expect(plainTextErr.status).toBe(502)
    expect(plainTextErr.message).toBe('Bad Gateway')

    const malformedJsonErr = await responseError(
      new Response('{not-json', {
        status: 500,
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(malformedJsonErr.status).toBe(500)
    expect(malformedJsonErr.message).toBe('HTTP 500')
  })
})
