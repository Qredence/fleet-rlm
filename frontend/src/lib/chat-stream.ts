import { Chat, useChat } from '@ai-sdk/react'
import { DefaultChatTransport, type ChatStatus } from 'ai'
import { useLayoutEffect } from 'react'
import { bearerHeaders, chatPath } from './api'
import { chatFetch } from './chat-fetch'
import type { FleetMessage } from './messages'

export type ChatSnapshot = {
  messages: FleetMessage[]
  status: ChatStatus
  error: Error | undefined
}

export function createStreamingChat({ id, token, messages, consumeRecentStop, onWaitingForStop, onComplete }: {
  id: string
  token: string
  messages: FleetMessage[]
  consumeRecentStop: () => boolean
  onWaitingForStop: (waiting: boolean) => void
  onComplete: () => void
}) {
  return new Chat<FleetMessage>({
    id,
    messages,
    transport: new DefaultChatTransport<FleetMessage>({
      api: chatPath(id),
      headers: bearerHeaders(token),
      prepareSendMessagesRequest: ({ messages: outgoing, body }) => {
        const latest = outgoing.findLast((message) => message.role === 'user')
        return {
          body: {
            messages: latest ? [{ role: latest.role, parts: latest.parts }] : [],
            pinned_skills: body?.pinned_skills ?? [],
          },
        }
      },
      fetch: chatFetch(consumeRecentStop, onWaitingForStop),
    }),
    onFinish: ({ isAbort, isDisconnect, isError }) => {
      if (!isAbort && !isDisconnect && !isError) onComplete()
    },
  })
}

export function ChatObserver({ chat, onChange }: {
  chat: Chat<FleetMessage>
  onChange: (snapshot: ChatSnapshot) => void
}) {
  const { messages, status, error } = useChat({ chat })
  useLayoutEffect(() => {
    onChange({ messages, status, error })
  }, [messages, status, error, onChange])
  return null
}
