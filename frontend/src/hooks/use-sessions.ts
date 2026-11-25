import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, errorMessage, type FleetApi, type Session, type SessionUpdate } from '@/lib/api'

const sessionKey = 'fleet-rlm.session'

type SessionLookup = {
  id: string
  session?: Session
  error?: string
  missing?: boolean
}

export function useSessions(api: FleetApi, routeId: string | undefined) {
  const [sessions, setSessions] = useState<Session[] | null>(null)
  const [listError, setListError] = useState('')
  const [remembered, setRemembered] = useState(() => localStorage.getItem(sessionKey) ?? '')
  const [lookup, setLookup] = useState<SessionLookup>()
  const [lookupAttempt, retryLookup] = useState(0)
  const [changing, setChanging] = useState(false)
  const listRequest = useRef<AbortController | null>(null)
  const changingRef = useRef(false)
  const requestedId = routeId ?? remembered
  const listed = sessions?.find(({ id }) => id === requestedId)
  const lookedUp = lookup?.id === requestedId ? lookup : undefined
  const needsLookup = Boolean(requestedId && !listed && (routeId || sessions))
  const session = listed ?? lookedUp?.session
    ?? (!routeId && (!requestedId || lookedUp?.missing) ? sessions?.[0] : undefined)

  const refresh = useCallback(() => {
    listRequest.current?.abort()
    const request = new AbortController()
    listRequest.current = request
    void api.listSessions(request.signal).then((items) => {
      if (request.signal.aborted) return
      setSessions(items)
      setListError('')
    }).catch((failure) => {
      if (!request.signal.aborted) setListError(errorMessage(failure))
    })
  }, [api])

  useEffect(() => {
    refresh()
    return () => listRequest.current?.abort()
  }, [refresh])

  useEffect(() => {
    if (!needsLookup) return
    const request = new AbortController()
    void api.getSession(requestedId, request.signal).then((result) => {
      if (!request.signal.aborted) setLookup({ id: requestedId, session: result })
    }).catch((failure) => {
      if (request.signal.aborted) return
      const missing = failure instanceof ApiError && (failure.status === 404 || failure.status === 422)
      setLookup({ id: requestedId, missing, error: missing ? 'This session could not be found.' : errorMessage(failure) })
    })
    return () => request.abort()
  }, [api, lookupAttempt, needsLookup, requestedId])

  const selectedId = session?.id
  if (selectedId && selectedId !== remembered) setRemembered(selectedId)
  useEffect(() => {
    if (remembered) localStorage.setItem(sessionKey, remembered)
    else localStorage.removeItem(sessionKey)
  }, [remembered])

  async function mutate<T>(request: () => Promise<T>) {
    if (changingRef.current) throw new Error('A session change is already in progress.')
    changingRef.current = true
    setChanging(true)
    try {
      const result = await request()
      listRequest.current?.abort()
      return result
    } finally {
      changingRef.current = false
      setChanging(false)
    }
  }

  async function create() {
    const created = await mutate(api.createSession)
    setSessions((items) => [created, ...(items ?? []).filter(({ id }) => id !== created.id)])
    setListError('')
    refresh()
    return created
  }

  async function update(id: string, changes: SessionUpdate) {
    const updated = await mutate(() => api.updateSession(id, changes))
    setSessions((items) => items?.map((item) => item.id === id ? updated : item) ?? null)
    setLookup((current) => current?.id === id ? { id, session: updated } : current)
    refresh()
    return updated
  }

  async function remove(id: string) {
    try {
      await mutate(() => api.deleteSession(id))
      setSessions((items) => items?.filter((item) => item.id !== id) ?? null)
      setLookup((current) => current?.id === id ? undefined : current)
      setRemembered((current) => current === id ? '' : current)
    } finally {
      refresh()
    }
  }

  return {
    sessions: sessions ?? [], session, changing, listError,
    listLoading: sessions === null && !listError,
    loading: !session && (needsLookup ? !lookedUp : sessions === null && !listError),
    error: !session ? lookedUp?.error ?? listError : '',
    missing: lookedUp?.missing ?? false,
    create, update, remove, refresh,
    retry: () => {
      setLookup(undefined)
      setListError('')
      retryLookup((value) => value + 1)
      refresh()
    },
  }
}
