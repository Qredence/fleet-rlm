import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import { useEffect, useRef, useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/api'

export function PdfPreview({ bytes }: { bytes: Uint8Array }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy>()
  const [pageNumber, setPageNumber] = useState(1)
  const [error, setError] = useState('')

  useEffect(() => {
    let closed = false
    let loading: PDFDocumentLoadingTask | undefined

    async function load() {
      const [pdfjs, worker] = await Promise.all([
        import('pdfjs-dist'),
        import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
      ])
      if (closed) return
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default
      // PDF.js transfers the buffer to its worker. Keep the original available for remounts.
      loading = pdfjs.getDocument({ data: bytes.slice(), maxImageSize: 16_000_000 })
      const document = await loading.promise
      if (!closed) setPdf(document)
    }

    void load().catch((failure) => { if (!closed) setError(errorMessage(failure)) })
    return () => {
      closed = true
      void loading?.destroy().catch(() => {})
    }
  }, [bytes])

  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
  if (!pdf) return <p role="status" className="p-4 text-muted-foreground">Loading PDF…</p>

  return (
    <>
      {pdf.numPages > 1 && (
        <div className="flex items-center justify-center gap-3 border-b px-2 py-1">
          <Button variant="ghost" size="icon-sm" aria-label="Previous page" disabled={pageNumber === 1} onClick={() => setPageNumber(pageNumber - 1)}>
            <ChevronLeft />
          </Button>
          <span className="text-xs tabular-nums" aria-live="polite">Page {pageNumber} of {pdf.numPages}</span>
          <Button variant="ghost" size="icon-sm" aria-label="Next page" disabled={pageNumber === pdf.numPages} onClick={() => setPageNumber(pageNumber + 1)}>
            <ChevronRight />
          </Button>
        </div>
      )}
      <PdfPage key={pageNumber} pdf={pdf} pageNumber={pageNumber} />
    </>
  )
}

function PdfPage({ pdf, pageNumber }: { pdf: PDFDocumentProxy; pageNumber: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let closed = false
    let drawing: RenderTask | undefined

    async function render(target: HTMLCanvasElement) {
      const page = await pdf.getPage(pageNumber)
      if (closed) return
      const { width, height } = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: Math.min(1280 / width, 1600 / height) })
      target.width = Math.max(1, Math.ceil(viewport.width))
      target.height = Math.max(1, Math.ceil(viewport.height))
      drawing = page.render({ canvas: target, viewport })
      await drawing.promise
      if (!closed) setReady(true)
    }

    void render(canvas).catch((failure) => { if (!closed) setError(errorMessage(failure)) })
    return () => {
      closed = true
      drawing?.cancel()
    }
  }, [pdf, pageNumber])

  if (error) return <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>

  return (
    <div className="grid max-h-[62dvh] place-items-center overflow-auto bg-muted p-2 sm:p-4">
      {!ready && <p role="status" className="p-4 text-muted-foreground">Rendering page…</p>}
      <canvas ref={canvasRef} hidden={!ready} role="img" aria-label={`PDF page ${pageNumber}`} className="h-auto max-w-full bg-white" />
    </div>
  )
}
