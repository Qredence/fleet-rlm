import { Check, Package, Paperclip, Plus } from 'lucide-react'
import { useId, useLayoutEffect, useRef, useState, type Dispatch, type KeyboardEvent, type ReactNode, type SetStateAction } from 'react'
import { Button } from '@/components/ui/button'
import { DeferredDropdownMenu } from '@/components/ui/deferred-dropdown-menu'
import { Popover, PopoverContent } from '@/components/ui/popover'
import { PromptInput } from '@/components/ui/prompt-input'
import type { Skill } from '@/lib/api'

type Mention = { start: number; end: number; query: string }

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

export function ChatComposer({ skills, pinned, setPinned, value, onValueChange, onSubmit, onStop, loading, attachments, onUpload, submitDisabled, modelSelector }: {
  skills: Skill[]
  pinned: string[]
  setPinned: Dispatch<SetStateAction<string[]>>
  value: string
  onValueChange: (value: string) => void
  onSubmit: (value: string) => Promise<void>
  onStop: () => void
  loading: boolean
  attachments: ReactNode
  onUpload?: (files: File[]) => void
  submitDisabled: boolean
  modelSelector: ReactNode
}) {
  const [mention, setMention] = useState<Mention | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const filePicker = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const pendingCaret = useRef<number | null>(null)
  const listId = useId()
  const matches = mention ? skills.filter(({ name }) => name.toLowerCase().includes(mention.query)) : []
  const currentIndex = Math.min(activeIndex, matches.length - 1)
  const activeId = mention && currentIndex >= 0 ? `${listId}-${currentIndex}` : undefined

  useLayoutEffect(() => {
    if (pendingCaret.current === null) return
    const caret = pendingCaret.current
    pendingCaret.current = null
    textarea.current?.focus({ preventScroll: true })
    textarea.current?.setSelectionRange(caret, caret)
  }, [value, mention])

  useLayoutEffect(() => {
    if (!activeId) return
    document.getElementById(activeId)?.scrollIntoView({ block: 'nearest' })
  }, [activeId])

  function updateMention(input: HTMLTextAreaElement) {
    setMention(skills.length ? mentionAtCaret(input) : null)
    setActiveIndex(0)
    if (list.current) list.current.scrollTop = 0
  }

  function setSkillPinned(name: string, checked: boolean) {
    setPinned((current) => {
      if (!checked) return current.filter((item) => item !== name)
      return current.includes(name) ? current : [...current, name]
    })
  }

  function pick(name: string) {
    // Validate the live caret in case a pointer press moved it after the last input event.
    const current = textarea.current && mentionAtCaret(textarea.current)
    if (!current || current.start !== mention?.start) {
      setMention(null)
      return
    }

    setSkillPinned(name, true)
    const suffix = value.slice(current.end)
    const completion = `@${name}${suffix ? '' : ' '}`
    pendingCaret.current = current.start + completion.length + (suffix.startsWith(' ') ? 1 : 0)
    onValueChange(value.slice(0, current.start) + completion + suffix)
    setMention(null)
  }

  function navigate(event: KeyboardEvent<HTMLElement>) {
    if (!mention || event.defaultPrevented || event.nativeEvent.isComposing) return

    if (event.key === 'Escape' || event.key === 'Tab') {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
      }
      setMention(null)
      return
    }

    if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (matches.length) {
        const delta = event.key === 'ArrowDown' ? 1 : -1
        setActiveIndex((currentIndex + delta + matches.length) % matches.length)
      }
      return
    }

    if (event.key === 'Enter') {
      event.preventDefault()
      if (matches[currentIndex]) pick(matches[currentIndex].name)
    }
  }

  return (
    <Popover
      open={Boolean(mention)}
      onOpenChange={(open) => { if (!open) setMention(null) }}
    >
      <PromptInput
        label="Message Fleet"
        placeholder="Message Fleet…"
        value={value}
        onValueChange={onValueChange}
        onSubmit={(text) => {
          setMention(null)
          return onSubmit(text)
        }}
        onStop={onStop}
        loading={loading}
        submitDisabled={submitDisabled}
        attachments={attachments}
        onFileDrop={onUpload}
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
          onKeyDown: navigate,
          onSelect: (event) => {
            const next = mentionAtCaret(event.currentTarget)
            setMention((current) => {
              if (!current) return null
              return next?.start === current.start && next.query === current.query ? current : null
            })
          },
        }}
        trailingAction={modelSelector}
        leadingAction={(
          <>
            <input
              ref={filePicker} type="file" multiple hidden aria-label="Attach workspace files"
              onChange={(event) => {
                const files = Array.from(event.currentTarget.files ?? [])
                event.currentTarget.value = ''
                onUpload?.(files)
              }}
            />
            <DeferredDropdownMenu
              trigger={(
                <Button variant="ghost" size="icon" aria-label="Add to chat" className="rounded-(--prompt-inner-radius)">
                  <Plus aria-hidden="true" />
                </Button>
              )}
              side="top" align="start" sideOffset={6}
              className="max-h-[min(20rem,var(--available-height))] w-56 max-w-(--available-width)"
              items={[{
                label: 'Files and attachments',
                icon: <Paperclip aria-hidden="true" />,
                disabled: !onUpload,
                className: 'min-h-9 px-2',
                onSelect: () => filePicker.current?.click(),
              }]}
              checkboxGroup={skills.length ? {
                label: 'Skills',
                options: skills.map(({ name, description }) => ({
                  id: name,
                  label: skillTitle(name),
                  description,
                  checked: pinned.includes(name),
                  onCheckedChange: (checked) => setSkillPinned(name, checked),
                })),
              } : undefined}
            />
            <PopoverContent
              aria-label="Skill picker"
              anchor={textarea}
              side="top"
              align="start"
              initialFocus={false}
              finalFocus={false}
              className="w-[32rem]"
            >
              <div
                id={listId}
                ref={list}
                role="listbox"
                aria-label="Skills"
                aria-multiselectable="true"
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
                      className="group flex min-h-11 cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm select-none hover:bg-hover data-active:bg-hover data-active:text-accent-foreground sm:min-h-8"
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
            </PopoverContent>
          </>
        )}
      />
    </Popover>
  )
}
