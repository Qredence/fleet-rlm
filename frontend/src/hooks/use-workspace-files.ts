import { useCallback, useEffect, useRef, useState } from 'react'
import { errorMessage, type FleetApi, type WorkspaceFile } from '@/lib/api'

type Upload = {
  id: number
  file: File
  state: 'idle' | 'uploading' | 'done' | 'error'
  error?: string
}

export function useWorkspaceFiles(api: FleetApi, sessionId: string, onFilesUploaded: () => void) {
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null)
  const [error, setError] = useState('')
  const [attachments, setAttachments] = useState<Upload[]>([])
  const listRequest = useRef<AbortController | null>(null)
  const uploadRequest = useRef<AbortController | null>(null)
  const nextId = useRef(0)

  const refresh = useCallback(() => {
    listRequest.current?.abort()
    const request = new AbortController()
    listRequest.current = request
    void api.listFiles(sessionId, request.signal).then((result) => {
      if (request.signal.aborted) return
      setFiles(result)
      setError('')
    }).catch((failure) => {
      if (request.signal.aborted) return
      setFiles((current) => current ?? [])
      setError(errorMessage(failure))
    })
  }, [api, sessionId])

  useEffect(() => {
    refresh()
    return () => {
      listRequest.current?.abort()
      uploadRequest.current?.abort()
    }
  }, [refresh])

  async function upload(batch: Upload[]) {
    if (uploadRequest.current || !batch.length) return
    const request = new AbortController()
    uploadRequest.current = request
    const batchIds = new Set(batch.map(({ id }) => id))
    setAttachments((current) => [
      ...current.filter(({ id }) => !batchIds.has(id)),
      ...batch,
    ])
    let uploaded = false
    try {
      for (const entry of batch) {
        if (request.signal.aborted) break
        const update = (changes: Partial<Upload>) => setAttachments((current) => current.map(
          (item) => item.id === entry.id ? { ...item, ...changes } : item,
        ))
        update({ state: 'uploading' })
        try {
          const record = await api.uploadFile(sessionId, entry.file, request.signal)
          if (request.signal.aborted) break
          uploaded = true
          listRequest.current?.abort()
          setFiles((current) => [...(current ?? []).filter(({ path }) => path !== record.path), record])
          update({ state: 'done' })
        } catch (failure) {
          if (!request.signal.aborted) update({ state: 'error', error: errorMessage(failure) })
        }
      }
      if (uploaded && !request.signal.aborted) {
        refresh()
        onFilesUploaded()
      }
    } finally {
      uploadRequest.current = null
    }
  }

  function add(batch: File[]) {
    void upload(batch.map((file) => ({ file, id: nextId.current++, state: 'idle' })))
  }

  function retry(id: number) {
    const entry = attachments.find((item) => item.id === id && item.state === 'error')
    if (entry) void upload([{ id, file: entry.file, state: 'idle' }])
  }

  function dismiss(id: number) {
    setAttachments((current) => current.filter((item) => item.id !== id))
  }

  function download(path: string) {
    setError('')
    void api.downloadFile(sessionId, path).catch((failure) => setError(errorMessage(failure)))
  }

  return {
    files, error, attachments, add, retry, dismiss, download, refresh,
    uploading: attachments.some(({ state }) => state === 'uploading' || state === 'idle'),
    hasFailed: attachments.some(({ state }) => state === 'error'),
    isUploading: () => uploadRequest.current !== null,
    clearCompleted: () => setAttachments((current) => current.filter(({ state }) => state !== 'done')),
  }
}

export type WorkspaceFiles = ReturnType<typeof useWorkspaceFiles>
