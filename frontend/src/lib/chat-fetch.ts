import { responseError } from './api'

const delays = [300, 600, 1000, 1500]
export const STOP_RETRY_WINDOW_MS = 10_000

function wait(ms: number, signal?: AbortSignal | null) {
  return new Promise<void>((resolve, reject) => {
    signal?.throwIfAborted()
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', abort)
      resolve()
    }, ms)
    function abort() {
      window.clearTimeout(timer)
      reject(signal?.reason)
    }
    signal?.addEventListener('abort', abort, { once: true })
  })
}

// The SDK owns the response stream. This wrapper only handles HTTP status before it opens.
export function chatFetch(
  canRetryAfterStop: () => boolean,
  setWaiting: (waiting: boolean) => void,
  onAccepted?: () => void,
): typeof fetch {
  return async (input, options) => {
    const canRetry = options?.method === 'POST' && canRetryAfterStop()
    let waiting = false
    try {
      let response = await fetch(input, options)
      if (canRetry && response.status === 409) {
        waiting = true
        setWaiting(true)
        for (const delay of delays) {
          await response.body?.cancel()
          await wait(delay, options.signal)
          response = await fetch(input, options)
          if (response.status !== 409) break
        }
      }
      if (!response.ok) throw await responseError(response)
      if (options?.method === 'POST') onAccepted?.()
      return response
    } finally {
      if (waiting) setWaiting(false)
    }
  }
}
