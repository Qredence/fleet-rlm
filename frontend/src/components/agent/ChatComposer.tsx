import { Popover } from '@base-ui/react/popover'
import { AtSign, Check, Package } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { PromptInput } from '@/components/ui/prompt-input'
import type { Skill } from '@/lib/api'

type Mention = { start: number; end: number; query: string }
type SkillPicker = { mode: 'button' } | { mode: 'mention'; mention: Mention }

function mentionAtCaret({ value, selectionStart, selectionEnd }: HTMLTextAreaElement): Mention | null {
  if (selectionStart !== selectionEnd) return null
  const match = /(?:^|[\s([{])@([\p{L}\p{N}_-]*)$/u.exec(value.slice(0, selectionStart))
  if (!match) return null

  const typed = match[1]
  const remaining = /^[\p{L}\p{N}_-]*/u.exec(value.slice(selectionStart))?.[0] ?? ''
  return {
    start: selectionStart - typed.length - 1,
    end: selectionStart + remaining.length,
    query: typed.toLowerCase(),
  }
}

function skillTitle(name: string) {
  const words = name.replace(/[-_]+/g, ' ').trim() || name
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function ChatComposer({ skills, pinned, setPinned, value, onValueChange, onSubmit, onStop, loading }: {
  skills: Skill[]
  pinned: string[]
  setPinned: (names: string[]) => void
  value: string
  onValueChange: (value: string) => void
  onSubmit: (value: string) => Promise<void>
  onStop: () => void
  loading: boolean
}) {
  const [picker, setPicker] = useState<SkillPicker | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const pendingCaret = useRef<number | null>(null)
  const typeahead = useRef({ text: '', time: 0 })
  const listId = useId()
  const mention = picker?.mode === 'mention' ? picker.mention : null
  const matches = mention ? skills.filter(({ name }) => name.toLowerCase().includes(mention.query)) : skills
  const currentIndex = Math.min(activeIndex, matches.length - 1)
  const activeId = picker && currentIndex >= 0 ? `${listId}-${currentIndex}` : undefined

  useLayoutEffect(() => {
    if (pendingCaret.current === null) return
    const caret = pendingCaret.current
    pendingCaret.current = null
    textarea.current?.focus({ preventScroll: true })
    textarea.current?.setSelectionRange(caret, caret)
  }, [value, picker])

  useLayoutEffect(() => {
    if (!activeId) return
    document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' })
  }, [activeId])

  function updateMention(input: HTMLTextAreaElement) {
    const next = skills.length ? mentionAtCaret(input) : null
    setPicker(next ? { mode: 'mention', mention: next } : null)
    setActiveIndex(0)
    if (list.current) list.current.scrollTop = 0
  }

  function pick(name: string) {
    if (!mention) {
      setPinned(pinned.includes(name) ? pinned.filter((item) => item !== name) : [...pinned, name])
      return
    }

    // Validate the live caret in case a pointer press moved it after the last input event.
    const current = textarea.current && mentionAtCaret(textarea.current)
    if (!current || current.start !== mention.start) {
      setPicker(null)
      return
    }

    if (!pinned.includes(name)) setPinned([...pinned, name])
    const suffix = value.slice(current.end)
    const completion = `@${name}${suffix ? '' : ' '}`
    pendingCaret.current = current.start + completion.length + (suffix.startsWith(' ') ? 1 : 0)
    onValueChange(value.slice(0, current.start) + completion + suffix)
    setPicker(null)
  }

  function navigate(event: KeyboardEvent<HTMLElement>) {
    if (!picker || event.defaultPrevented || event.nativeEvent.isComposing) return

    if (event.key === 'Escape' || (mention && event.key === 'Tab')) {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        if (!mention) trigger.current?.focus({ preventScroll: true })
      }
      setPicker(null)
      return
    }

    if (mention && (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey)) return

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (matches.length) {
        const delta = event.key === 'ArrowDown' ? 1 : -1
        setActiveIndex((currentIndex + delta + matches.length) % matches.length)
      }
      return
    }

    if ((event.key === 'Enter' && !event.shiftKey) || (!mention && event.key === ' ')) {
      event.preventDefault()
      if (matches[currentIndex]) pick(matches[currentIndex].name)
      return
    }

    if (mention) return
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      setActiveIndex(event.key === 'Home' ? 0 : matches.length - 1)
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const time = Date.now()
      const key = event.key.toLowerCase()
      const text = time - typeahead.current.time < 500 ? typeahead.current.text + key : key
      const index = matches.findIndex(({ name }) => skillTitle(name).toLowerCase().startsWith(text))
      typeahead.current = { text, time }
      if (index >= 0) setActiveIndex(index)
    }
  }

  return (
    <Popover.Root
      open={Boolean(picker)}
      onOpenChange={(open, details) => {
        if (!open && !mention && details.reason === 'escape-key') trigger.current?.focus({ preventScroll: true })
        setPicker(open ? { mode: 'button' } : null)
        setActiveIndex(0)
        typeahead.current = { text: '', time: 0 }
      }}
    >
      <PromptInput
        label="Message Fleet"
        placeholder="Message Fleet…"
        value={value}
        onValueChange={onValueChange}
        onSubmit={(text) => {
          setPicker(null)
          return onSubmit(text)
        }}
        onStop={onStop}
        loading={loading}
        textareaRef={textarea}
        textareaProps={{
          'aria-autocomplete': skills.length ? 'list' : undefined,
          'aria-controls': mention ? listId : undefined,
          'aria-activedescendant': mention ? activeId : undefined,
          onChange: (event) => {
            if ('isComposing' in event.nativeEvent && event.nativeEvent.isComposing) return
            updateMention(event.currentTarget)
          },
          onCompositionEnd: (event) => updateMention(event.currentTarget),
          onKeyDown: (event) => { if (mention) navigate(event) },
          onSelect: (event) => {
            const next = mentionAtCaret(event.currentTarget)
            setPicker((current) => {
              if (current?.mode !== 'mention') return current
              return next?.start === current.mention.start && next.query === current.mention.query ? current : null
            })
          },
        }}
        leadingAction={skills.length > 0 && (
          <Popover.Trigger ref={trigger} render={<Button type="button" variant="ghost" size="sm" />}>
            <AtSign data-icon="inline-start" aria-hidden="true" />
            Skills
            {pinned.length > 0 && <Badge variant="secondary">{pinned.length}</Badge>}
          </Popover.Trigger>
        )}
      />
      <Popover.Portal>
        <Popover.Positioner side="top" align="start" sideOffset={4} className="z-50">
          <Popover.Popup
            aria-label="Skill picker"
            initialFocus={mention ? false : list}
            finalFocus={false}
            className="w-[32rem] max-w-(--available-width) rounded-xl bg-popover text-popover-foreground shadow-md ring-1 ring-foreground/10 outline-none"
          >
            <div
              id={listId}
              ref={list}
              role="listbox"
              aria-label="Skills"
              aria-multiselectable="true"
              aria-activedescendant={activeId}
              tabIndex={mention ? -1 : 0}
              onKeyDown={navigate}
              className="max-h-[min(20rem,var(--available-height))] overflow-x-hidden overflow-y-auto overscroll-contain scroll-py-1 rounded-xl p-1 outline-none scrollbar-thin"
            >
              {matches.map(({ name, description }, index) => {
                const title = skillTitle(name)
                const selected = pinned.includes(name)

                return (
                  <div
                    id={`${listId}-${index}`}
                    key={name}
                    role="option"
                    aria-label={title}
                    aria-description={description}
                    aria-selected={selected}
                    title={`@${name}\n${description}`}
                    data-active={index === currentIndex || undefined}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => pick(name)}
                    className="group flex min-h-11 cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm select-none hover:bg-accent data-active:bg-accent data-active:text-accent-foreground sm:min-h-8"
                  >
                    <Package aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                    <div className="flex min-w-0 flex-1 flex-col sm:flex-row sm:items-baseline sm:gap-2">
                      <span className="truncate font-medium sm:max-w-[60%] sm:shrink-0">{title}</span>
                      {description && <span className="truncate text-xs text-muted-foreground">{description}</span>}
                    </div>
                    <Check aria-hidden="true" className="size-4 shrink-0 invisible group-aria-selected:visible" />
                  </div>
                )
              })}
            </div>
            {matches.length === 0 && <p role="status" className="px-3 py-2 text-sm text-muted-foreground">No matching skills</p>}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}
