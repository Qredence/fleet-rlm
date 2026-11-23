// Adapted from https://beui.dev/components/agents/prompt-input
import { ArrowUp, Square } from 'lucide-react'
import { useLayoutEffect, useRef, useState, type ComponentProps, type FormEvent, type ReactNode, type RefObject } from 'react'
import { Button } from '@/components/ui/button'
import { SurfaceProvider } from '@/lib/surface-context'
import { useSurfaceLayer } from '@/lib/use-surface-layer'
import { cn } from '@/lib/utils'

const textareaTypography = 'px-2 py-1.5 text-sm leading-6 wrap-anywhere whitespace-pre-wrap'

interface PromptInputProps {
  value: string
  onValueChange: (value: string) => void
  onSubmit: (value: string) => void | Promise<void>
  onStop: () => void
  loading: boolean
  label: string
  placeholder?: string
  leadingAction?: ReactNode
  trailingAction?: ReactNode
  attachments?: ReactNode
  onFileDrop?: (files: File[]) => void
  submitDisabled?: boolean
  textareaRef?: RefObject<HTMLTextAreaElement | null>
  textareaProps?: Pick<ComponentProps<'textarea'>,
    'aria-activedescendant' | 'aria-autocomplete' | 'aria-controls' |
    'onChange' | 'onCompositionEnd' | 'onKeyDown' | 'onSelect'
  >
}

export function PromptInput({
  value, onValueChange, onSubmit, onStop, loading, label, placeholder, leadingAction, trailingAction,
  textareaRef: forwardedRef, textareaProps, attachments, onFileDrop, submitDisabled,
}: PromptInputProps) {
  const localRef = useRef<HTMLTextAreaElement>(null)
  const textareaRef = forwardedRef ?? localRef
  const measurementRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  const { level, surfaceClass } = useSurfaceLayer(1, 2)
  const text = value.trim()

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    const measurement = measurementRef.current
    if (!textarea || !measurement) return

    const resizeTextarea = () => {
      const height = `${measurement.scrollHeight}px`
      if (textarea.style.height !== height) textarea.style.height = height
    }

    resizeTextarea()
    if (typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(resizeTextarea)
    observer.observe(measurement)
    return () => observer.disconnect()
  }, [textareaRef])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!text || loading || submitDisabled) return
    void onSubmit(text)
    textareaRef.current?.focus({ preventScroll: true })
  }

  return (
    <SurfaceProvider value={level}>
      <form
        data-slot="prompt-input"
        data-surface={level}
        onSubmit={submit}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes('Files')) return
          event.preventDefault()
          event.dataTransfer.dropEffect = onFileDrop ? 'copy' : 'none'
          setDragging(Boolean(onFileDrop))
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
        }}
        onDrop={(event) => {
          if (!event.dataTransfer.types.includes('Files')) return
          event.preventDefault()
          setDragging(false)
          onFileDrop?.(Array.from(event.dataTransfer.files))
        }}
        className={cn(
          'relative w-full min-w-0 rounded-2xl border p-2 transition-colors focus-within:border-ring motion-reduce:transition-none',
          surfaceClass,
          dragging && 'border-ring',
        )}
      >
        {attachments}
        <div
          ref={measurementRef}
          aria-hidden="true"
          className={cn('pointer-events-none invisible absolute inset-x-2 top-2', textareaTypography)}
        >
          {`${value}\u200b`}
        </div>
        <textarea
          {...textareaProps}
          ref={textareaRef}
          value={value}
          placeholder={placeholder}
          aria-label={label}
          rows={2}
          onChange={(event) => {
            onValueChange(event.currentTarget.value)
            textareaProps?.onChange?.(event)
          }}
          onKeyDown={(event) => {
            textareaProps?.onKeyDown?.(event)
            if (event.defaultPrevented || event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
            event.preventDefault()
            event.currentTarget.form?.requestSubmit()
          }}
          className={cn(
            'block max-h-[calc(6lh+0.75rem)] min-h-[calc(2lh+0.75rem)] w-full resize-none overflow-y-auto bg-transparent text-foreground outline-none placeholder:text-muted-foreground',
            textareaTypography,
          )}
        />
        <div className="mt-1 flex min-w-0 items-center gap-1.5">
          {leadingAction}
          <div className="ml-auto flex min-w-0 items-center gap-1.5">
            {trailingAction}
            <Button
              type={loading ? 'button' : 'submit'}
              size="icon"
              variant={loading ? 'outline' : 'default'}
              disabled={!loading && (!text || submitDisabled)}
              aria-label={loading ? 'Stop' : 'Send'}
              onClick={(event) => {
                if (!loading) return
                // Stopping can switch the button back to submit during the same click.
                event.preventDefault()
                onStop()
              }}
              className="shrink-0 rounded-full"
            >
              <span aria-hidden="true" data-loading={loading || undefined} className="group/action grid place-items-center">
                <ArrowUp className="col-start-1 row-start-1 transition-[scale,opacity] duration-150 group-data-loading/action:scale-75 group-data-loading/action:opacity-0 motion-reduce:transition-none" />
                <Square className="col-start-1 row-start-1 scale-75 opacity-0 transition-[scale,opacity] duration-150 group-data-loading/action:scale-100 group-data-loading/action:opacity-100 motion-reduce:transition-none" />
              </span>
            </Button>
          </div>
        </div>
      </form>
    </SurfaceProvider>
  )
}
