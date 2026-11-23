import { Download, FileText, PackagePlus, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AspectRatio } from '@/components/ui/aspect-ratio'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage, type FleetApi, type Skill } from '@/lib/api'
import { formatFileSize } from '@/lib/file-tree'
import { PdfPreview } from './PdfPreview'

const textLimit = 128 * 1024
const mediaLimit = 20 * 1024 * 1024
const skillLimit = 64 * 1024
const mediaTypes = new Map([
  ['png', 'image/png'], ['jpg', 'image/jpeg'], ['jpeg', 'image/jpeg'], ['gif', 'image/gif'],
  ['webp', 'image/webp'], ['avif', 'image/avif'], ['bmp', 'image/bmp'], ['svg', 'image/svg+xml'],
  ['pdf', 'application/pdf'],
])
const textExtensions = new Set([
  'txt', 'md', 'markdown', 'csv', 'tsv', 'json', 'jsonl', 'ndjson', 'log', 'xml', 'html',
  'yaml', 'yml', 'toml', 'ini', 'sql', 'py', 'js', 'ts', 'css', 'sh', 'ipynb',
])

type Preview =
  | { kind: 'text'; content: string; truncated: boolean; skillDigest?: string }
  | { kind: 'image'; url: string }
  | { kind: 'pdf'; bytes: Uint8Array }
  | { kind: 'unavailable'; message: string }
  | { kind: 'error'; message: string }

async function readPrefix(response: Response, limit: number) {
  const bytes = new Uint8Array(limit)
  const reader = response.body?.getReader()
  if (!reader) return { bytes: bytes.subarray(0, 0), truncated: false }
  let offset = 0
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) return { bytes: bytes.subarray(0, offset), truncated: false }
      const part = value.subarray(0, limit - offset)
      bytes.set(part, offset)
      offset += part.length
      if (part.length < value.length) {
        await reader.cancel()
        return { bytes, truncated: true }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export function FilePreview({ api, sessionId, path, onClose, onDownload, onSkillInstalled }: {
  api: FleetApi
  sessionId: string
  path: string
  onClose?: () => void
  onDownload: () => Promise<void>
  onSkillInstalled: (skill: Skill) => void
}) {
  const [preview, setPreview] = useState<Preview>()
  const [downloadError, setDownloadError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [installed, setInstalled] = useState<Skill>()
  const [installError, setInstallError] = useState('')
  const name = path.split('/').at(-1) ?? path
  const dot = name.lastIndexOf('.')
  const extension = dot < 0 ? '' : name.slice(dot + 1).toLowerCase()
  const isSkillArtifact = path.startsWith('artifacts/') && name === 'SKILL.md'
  // Review the exact instructions and YAML that will be installed.
  const isMarkdown = !isSkillArtifact && (extension === 'md' || extension === 'markdown')

  useEffect(() => {
    const request = new AbortController()
    const mediaType = mediaTypes.get(extension)
    const isText = textExtensions.has(extension)
    let objectUrl: string | undefined

    async function load() {
      if (!mediaType && !isText) {
        setPreview({ kind: 'unavailable', message: 'Download this file to open it.' })
        return
      }
      const response = await api.getFile(sessionId, path, request.signal)
      const limit = isText ? textLimit : mediaLimit
      if (mediaType && Number(response.headers.get('content-length')) > limit) {
        await response.body?.cancel()
        if (!request.signal.aborted) {
          setPreview({ kind: 'unavailable', message: `Files larger than ${formatFileSize(limit)} are available to download.` })
        }
        return
      }
      const { bytes, truncated } = await readPrefix(response, limit)
      if (request.signal.aborted) return
      if (isText) {
        const content = new TextDecoder().decode(bytes, { stream: truncated })
        let skillDigest: string | undefined
        if (isSkillArtifact && !truncated && bytes.length <= skillLimit && globalThis.crypto?.subtle) {
          const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
          skillDigest = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
        }
        if (!request.signal.aborted) setPreview({ kind: 'text', content, truncated, skillDigest })
      } else if (truncated) {
        setPreview({ kind: 'unavailable', message: `Files larger than ${formatFileSize(limit)} are available to download.` })
      } else if (mediaType === 'application/pdf') {
        setPreview({ kind: 'pdf', bytes })
      } else {
        objectUrl = URL.createObjectURL(new Blob([bytes], { type: mediaType }))
        setPreview({ kind: 'image', url: objectUrl })
      }
    }

    void load().catch((failure) => {
      if (!request.signal.aborted) setPreview({ kind: 'error', message: errorMessage(failure) })
    })
    return () => {
      request.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [api, extension, isSkillArtifact, path, sessionId])

  async function download() {
    setDownloadError('')
    setDownloading(true)
    try {
      await onDownload()
    } catch (failure) {
      setDownloadError(errorMessage(failure))
    } finally {
      setDownloading(false)
    }
  }

  async function install(sha256: string) {
    setInstallError('')
    setInstalling(true)
    try {
      const skill = await api.installSkill(sessionId, path, sha256)
      onSkillInstalled(skill)
      setInstalled(skill)
    } catch (failure) {
      setInstallError(errorMessage(failure))
    } finally {
      setInstalling(false)
    }
  }

  const skillDigest = preview?.kind === 'text' ? preview.skillDigest : undefined

  return (
    <section aria-label="File preview" className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
        <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium" title={path}>{name}</h3>
        {skillDigest && !installed && (
          <Button size="xs" disabled={installing} onClick={() => void install(skillDigest)}>
            <PackagePlus data-icon="inline-start" /> {installing ? 'Installing…' : 'Install skill'}
          </Button>
        )}
        <Button variant="ghost" size="icon-sm" aria-label="Download file" title="Download file"
          disabled={downloading} onClick={() => void download()}>
          <Download />
        </Button>
        {onClose && (
          <Button variant="ghost" size="icon-sm" aria-label="Close file preview" title="Close file preview" onClick={onClose}>
            <X />
          </Button>
        )}
      </header>
      {downloadError && <Alert variant="destructive"><AlertDescription>{downloadError}</AlertDescription></Alert>}
      {installError && <Alert variant="destructive"><AlertDescription>{installError}</AlertDescription></Alert>}
      {installed && (
        <p role="status" className="shrink-0 border-b px-3 py-2 text-sm">
          Installed @{installed.name}. It is available in Skills for all sessions.
        </p>
      )}
      <div className="min-h-0 min-w-0 flex-1 overflow-auto overscroll-contain bg-background">
        <div className="min-h-32 min-w-0">
          {!preview && <Skeleton role="status" aria-label="Loading preview" className="h-32 motion-reduce:animate-none" />}
          {preview?.kind === 'unavailable' && <p className="p-4 text-muted-foreground">{preview.message}</p>}
          {preview?.kind === 'error' && <Alert variant="destructive"><AlertDescription>{preview.message}</AlertDescription></Alert>}
          {preview?.kind === 'text' && (
            <>
              {preview.truncated && <p className="border-b px-4 py-2 text-xs text-muted-foreground">Showing the first 128 KiB.</p>}
              {isMarkdown && preview.content ? (
                <div aria-label="File contents" tabIndex={0} className="min-w-0 wrap-anywhere p-4">
                  <div className="markdown min-w-0 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto">
                    <Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['a', 'img']} unwrapDisallowed>
                      {preview.content}
                    </Markdown>
                  </div>
                </div>
              ) : (
                <pre aria-label="File contents" tabIndex={0} className="whitespace-pre-wrap wrap-anywhere p-4 font-mono text-xs leading-relaxed">
                  {preview.content || 'This file is empty.'}
                </pre>
              )}
            </>
          )}
          {preview?.kind === 'image' && (
            <div className="mx-auto max-w-[min(100%,112dvh)]">
              <AspectRatio ratio={16 / 9}>
                <img
                  src={preview.url}
                  alt={name}
                  className="size-full object-contain"
                  onError={() => setPreview({ kind: 'error', message: 'The browser could not display this image.' })}
                />
              </AspectRatio>
            </div>
          )}
          {preview?.kind === 'pdf' && <PdfPreview bytes={preview.bytes} />}
        </div>
      </div>
    </section>
  )
}
