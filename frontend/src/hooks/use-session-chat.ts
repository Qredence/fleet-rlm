import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { errorMessage } from '@/lib/api'
import { STOP_RETRY_WINDOW_MS } from '@/lib/chat-fetch'
import type { ChatObserver, ChatSnapshot, createStreamingChat } from '@/lib/chat-stream'
import type { FleetMessage } from '@/lib/messages'

type StreamingChat = ReturnType<typeof createStreamingChat>
type PendingSend = {
  chat?: StreamingChat
  previousMessages?: FleetMessage[]
  accepted: boolean
  onAccepted: () => void
}

export function useSessionChat({ id, token, initialMessages, recentStops, onAccepted, onComplete }: {
  id: string
  token: string
  initialMessages: FleetMessage[] | null
  recentStops: RefObject<Map<string, number>>
  onAccepted: () => void
  onComplete: () => void
}) {
  const [stream, setStream] = useState<{ chat: StreamingChat; Observer: typeof ChatObserver }>()
  const [snapshot, setSnapshot] = useState<ChatSnapshot>()
  const [preparing, setPreparing] = useState(false)
  const [waitingForStop, setWaitingForStop] = useState(false)
  const [sendError, setSendError] = useState('')
  const pending = useRef<PendingSend | null>(null)

  const stopStream = useCallback((chat: StreamingChat) => {
    recentStops.current.set(id, Date.now())
    void chat.stop()
  }, [id, recentStops])

  function stop() {
    const request = pending.current
    if (request?.chat) {
      stopStream(request.chat)
    } else if (request) {
      pending.current = null
      setPreparing(false)
    }
  }

  useEffect(() => () => {
    const request = pending.current
    pending.current = null
    if (request?.chat) stopStream(request.chat)
  }, [stopStream])

  async function send(text: string, pinned: string[], onRequestAccepted: () => void) {
    if (pending.current || initialMessages === null) return
    const request: PendingSend = { accepted: false, onAccepted: onRequestAccepted }
    pending.current = request
    setSendError('')
    try {
      let chat = stream?.chat
      if (!chat) {
        setPreparing(true)
        const { createStreamingChat, ChatObserver } = await import('@/lib/chat-stream')
        // The import can finish after Stop or after switching sessions.
        if (pending.current !== request) return
        chat = createStreamingChat({
          id, token, messages: initialMessages, onAccepted, onComplete,
          onRequestAccepted: () => {
            const active = pending.current
            if (active) {
              active.accepted = true
              active.onAccepted()
            }
          },
          consumeRecentStop: () => {
            const stoppedAt = recentStops.current.get(id)
            recentStops.current.delete(id)
            return stoppedAt !== undefined && Date.now() - stoppedAt < STOP_RETRY_WINDOW_MS
          },
          onWaitingForStop: setWaitingForStop,
        })
        setStream({ chat, Observer: ChatObserver })
      }

      request.chat = chat
      request.previousMessages = chat.messages
      const sending = chat.sendMessage({ text }, { body: { pinned_skills: pinned } })
      setSnapshot({ messages: chat.messages, status: chat.status, error: chat.error })
      setPreparing(false)
      await sending
    } catch (failure) {
      if (pending.current === request) {
        setSendError(request.chat ? errorMessage(failure) : 'Could not load chat. Your draft is still here. Try Send again.')
      }
    } finally {
      if (pending.current === request) {
        const { chat, previousMessages, accepted } = request
        if (chat && previousMessages && !accepted) {
          chat.messages = previousMessages
          setSnapshot({ messages: chat.messages, status: chat.status, error: chat.error })
        }
        pending.current = null
        setPreparing(false)
      }
    }
  }

  const status = snapshot?.status ?? 'ready'
  const active = preparing || status === 'submitted' || status === 'streaming'
  return {
    messages: snapshot?.messages ?? initialMessages ?? [], status, error: snapshot?.error,
    active, preparing, waitingForStop, sendError, stream, setSnapshot, send, stop,
    isBusy: () => pending.current !== null,
  }
}
