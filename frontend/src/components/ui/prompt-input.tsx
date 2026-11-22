// Adapted from https://beui.dev/components/agents/prompt-input
import { ArrowUp, Square } from 'lucide-react'
import { AnimatePresence, domAnimation, LazyMotion, useReducedMotion } from 'motion/react'
import * as m from 'motion/react-m'
import { useLayoutEffect, useRef, type ComponentProps, type FormEvent, type ReactNode, type RefObject } from 'react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const textareaTypography = 'px-2 py-1.5 text-sm leading-6 wrap-anywhere whitespace-pre-wrap'
const iconTransition = { type: 'spring', stiffness: 460, damping: 30, mass: 0.55 } as const

interface PromptInputProps {
  value: string
  onValueChange: (value: string) => void
  onSubmit: (value: string) => void | Promise<void>
  onStop: () => void
  loading: boolean
  label: string
  placeholder?: string
  leadingAction?: ReactNode
  textareaRef?: RefObject<HTMLTextAreaElement | null>
  textareaProps?: Pick<ComponentProps<'textarea'>,
    'aria-activedescendant' | 'aria-autocomplete' | 'aria-controls' |
    'onChange' | 'onCompositionEnd' | 'onKeyDown' | 'onSelect'
  >
}

export function PromptInput({
  value, onValueChange, onSubmit, onStop, loading, label, placeholder, leadingAction,
  textareaRef: forwardedRef, textareaProps,
}: PromptInputProps) {
  const reduceMotion = useReducedMotion()
  const localRef = useRef<HTMLTextAreaElement>(null)
  const textareaRef = forwardedRef ?? localRef
  const measurementRef = useRef<HTMLDivElement>(null)
  const text = value.trim()
  const ActionIcon = loading ? Square : ArrowUp

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
    if (!text || loading) return
    void onSubmit(text)
    textareaRef.current?.focus({ preventScroll: true })
  }

  return (
    <form
      data-slot="prompt-input"
      onSubmit={submit}
      className="relative w-full rounded-2xl border bg-background p-2 transition-colors focus-within:border-ring motion-reduce:transition-none"
    >
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
      <div className="mt-1 flex min-w-0 items-center gap-2">
        {leadingAction}
        <Button
          type={loading ? 'button' : 'submit'}
          size="icon"
          variant={loading ? 'outline' : 'default'}
          disabled={!loading && !text}
          aria-label={loading ? 'Stop' : 'Send'}
          onClick={(event) => {
            if (!loading) return
            // Stopping can switch the button back to submit during the same click.
            event.preventDefault()
            onStop()
          }}
          className="ml-auto rounded-full"
        >
          {reduceMotion ? (
            <ActionIcon aria-hidden="true" />
          ) : (
            <LazyMotion features={domAnimation} strict>
              <AnimatePresence initial={false} mode="popLayout">
                <m.span
                  key={loading ? 'stop' : 'send'}
                  aria-hidden="true"
                  initial={{ opacity: 0, y: 3, scale: 0.8 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: -3, scale: 0.8 }}
                  transition={iconTransition}
                  className="grid place-items-center"
                >
                  <ActionIcon />
                </m.span>
              </AnimatePresence>
            </LazyMotion>
          )}
        </Button>
      </div>
    </form>
  )
}
