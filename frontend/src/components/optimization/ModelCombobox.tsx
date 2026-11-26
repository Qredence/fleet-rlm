import { Check, ChevronDown, Plus, RefreshCw } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { FluidHoverHighlight } from '@/components/fluid-hover-highlight'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useFluidHover, useRegisterFluidHoverItem } from '@/hooks/use-fluid-hover'
import { fontWeights } from '@/lib/font-weight'
import { useShape } from '@/lib/shape-context'
import { typeClass } from '@/lib/type-scale'
import { cn } from '@/lib/utils'

export interface ModelComboboxOption {
  id: string
  label?: string
  owned_by?: string
}

export interface ModelComboboxProps {
  value: string
  onChange: (modelId: string) => void
  models: Array<ModelComboboxOption>
  loading?: boolean
  onRefresh?: () => void
  placeholder?: string
  disabled?: boolean
  id?: string
  ariaLabel?: string
  emptyHint?: string
}

interface ComboboxRow {
  kind: 'model' | 'custom'
  id: string
  label: string
  secondary?: string
  ownedBy?: string
}

function ModelComboboxRow({
  row,
  index,
  optionId,
  selected,
  active,
  registerItem,
  onSelect,
}: {
  row: ComboboxRow
  index: number
  optionId: string
  selected: boolean
  active: boolean
  registerItem: (index: number, element: HTMLElement | null) => void
  onSelect: (modelId: string) => void
}) {
  const rowRef = useRef<HTMLDivElement>(null)
  const shape = useShape()

  useRegisterFluidHoverItem(registerItem, index, rowRef)

  useEffect(() => {
    if (active && rowRef.current && typeof rowRef.current.scrollIntoView === 'function') {
      rowRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [active])

  const highlighted = active || selected

  return (
    <div
      ref={rowRef}
      id={optionId}
      role="option"
      aria-selected={selected}
      data-fluid-hover-index={index}
      data-active={active || undefined}
      onMouseDown={(event) => {
        // Keep DOM focus on the combobox input when picking a row.
        event.preventDefault()
      }}
      onClick={() => onSelect(row.id)}
      className={cn(
        'relative z-10 flex w-full cursor-pointer items-center justify-between gap-2 px-2.5 py-1.5 text-left select-none',
        shape.item,
        highlighted ? 'text-foreground' : 'text-muted-foreground',
      )}
    >
      <span className="flex min-w-0 flex-1 flex-col">
        {row.kind === 'custom' ? (
          <span className={cn(typeClass('body'), 'inline-flex items-center gap-1.5 truncate text-foreground')}>
            <Plus className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="truncate">
              Use custom model <span className="font-mono">{row.label}</span>
            </span>
          </span>
        ) : (
          <>
            <span className="inline-grid min-w-0">
              <span
                aria-hidden="true"
                className={cn(typeClass('body'), 'invisible col-start-1 row-start-1 truncate')}
                style={{ fontVariationSettings: fontWeights.semibold }}
              >
                {row.label}
              </span>
              <span
                className={cn(
                  typeClass('body'),
                  'col-start-1 row-start-1 truncate transition-[color,font-variation-settings] duration-80',
                )}
                style={{
                  fontVariationSettings: selected ? fontWeights.semibold : fontWeights.normal,
                }}
              >
                {row.label}
              </span>
            </span>
            {(row.secondary || row.ownedBy) && (
              <span className={cn(typeClass('caption'), 'flex items-center gap-1.5 truncate text-muted-foreground')}>
                {row.secondary && <span className="truncate font-mono">{row.secondary}</span>}
                {row.secondary && row.ownedBy && <span aria-hidden="true">·</span>}
                {row.ownedBy && <span className="truncate">{row.ownedBy}</span>}
              </span>
            )}
          </>
        )}
      </span>

      {selected && (
        <Check className="size-3.5 shrink-0 text-foreground" aria-hidden="true" />
      )}
    </div>
  )
}

export function ModelCombobox({
  value,
  onChange,
  models,
  loading = false,
  onRefresh,
  placeholder = 'Select or type a model ID…',
  disabled = false,
  id,
  ariaLabel,
  emptyHint,
}: ModelComboboxProps) {
  const generatedId = useId()
  const inputId = id ?? `${generatedId}-input`
  const listboxId = `${generatedId}-listbox`

  const [open, setOpen] = useState(false)
  const [isFiltering, setIsFiltering] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)

  const anchorRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const shape = useShape()
  const hover = useFluidHover(listRef, { axis: 'y' })
  const { setActiveIndex: setHoverIndex, remeasure } = hover

  const trimmed = value.trim()
  const query = isFiltering ? trimmed.toLowerCase() : ''

  const filteredModels = useMemo(() => {
    if (!query) return models
    return models.filter((item) => {
      const haystack = `${item.id} ${item.label ?? ''} ${item.owned_by ?? ''}`.toLowerCase()
      return haystack.includes(query)
    })
  }, [models, query])

  const hasExactMatch = useMemo(
    () => Boolean(trimmed) && models.some((item) => item.id === trimmed),
    [models, trimmed],
  )

  const rows = useMemo<ComboboxRow[]>(() => {
    const items: ComboboxRow[] = filteredModels.map((model) => {
      const label = model.label?.trim() || model.id
      return {
        kind: 'model',
        id: model.id,
        label,
        secondary: label !== model.id ? model.id : undefined,
        ownedBy: model.owned_by,
      }
    })
    if (trimmed && !hasExactMatch) {
      items.push({
        kind: 'custom',
        id: trimmed,
        label: trimmed,
      })
    }
    return items
  }, [filteredModels, hasExactMatch, trimmed])

  const clampedActiveIndex =
    rows.length === 0 ? -1 : activeIndex >= 0 && activeIndex < rows.length ? activeIndex : 0

  // Sync fluid hover highlight with keyboard active index when open.
  useEffect(() => {
    if (!open) {
      setHoverIndex(null)
      return
    }
    remeasure()
    setHoverIndex(clampedActiveIndex >= 0 ? clampedActiveIndex : null)
  }, [clampedActiveIndex, open, remeasure, setHoverIndex])

  const selectModel = (modelId: string) => {
    onChange(modelId)
    setOpen(false)
    setIsFiltering(false)
  }

  const optionIdFor = (index: number) => `${listboxId}-option-${index}`

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <Popover
        open={open && !disabled}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen)
          if (!nextOpen) setIsFiltering(false)
        }}
      >
        <div ref={anchorRef} className="relative flex min-w-0 flex-1 items-center">
          <Input
            ref={inputRef}
            id={inputId}
            type="text"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded={open && !disabled}
            aria-controls={listboxId}
            aria-activedescendant={
              open && !disabled && clampedActiveIndex >= 0
                ? optionIdFor(clampedActiveIndex)
                : undefined
            }
            aria-label={ariaLabel}
            value={value}
            disabled={disabled}
            placeholder={placeholder}
            autoComplete="off"
            spellCheck={false}
            className="pr-8"
            onFocus={() => {
              if (disabled) return
              setIsFiltering(false)
              setOpen(true)
              setActiveIndex(0)
            }}
            onChange={(event) => {
              onChange(event.target.value)
              setIsFiltering(true)
              setOpen(true)
              setActiveIndex(0)
            }}
            onKeyDown={(event) => {
              if (disabled) return
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                if (!open) {
                  setIsFiltering(false)
                  setOpen(true)
                  setActiveIndex(0)
                } else if (rows.length > 0) {
                  setActiveIndex((prev) => {
                    const current = prev >= 0 && prev < rows.length ? prev : -1
                    return (current + 1) % rows.length
                  })
                }
              } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                if (!open) {
                  setIsFiltering(false)
                  setOpen(true)
                  setActiveIndex(Math.max(0, rows.length - 1))
                } else if (rows.length > 0) {
                  setActiveIndex((prev) => {
                    const current = prev >= 0 && prev < rows.length ? prev : 0
                    return (current - 1 + rows.length) % rows.length
                  })
                }
              } else if (event.key === 'Enter' && open && clampedActiveIndex >= 0) {
                const chosen = rows[clampedActiveIndex]
                if (chosen) {
                  event.preventDefault()
                  selectModel(chosen.id)
                }
              } else if (event.key === 'Escape' && open) {
                event.preventDefault()
                event.stopPropagation()
                setOpen(false)
                setIsFiltering(false)
              }
            }}
          />

          <Tooltip open={open ? false : undefined}>
            <TooltipTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  tabIndex={-1}
                  disabled={disabled}
                  aria-label={open ? 'Close model list' : 'Browse models'}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    if (disabled) return
                    setIsFiltering(false)
                    setOpen((prev) => {
                      const next = !prev
                      if (next) setActiveIndex(0)
                      return next
                    })
                    inputRef.current?.focus()
                  }}
                  className="absolute right-1 text-muted-foreground hover:text-foreground"
                />
              }
            >
              <ChevronDown
                className={cn('size-3.5 transition-transform duration-160', open && 'rotate-180')}
                aria-hidden="true"
              />
            </TooltipTrigger>
            <TooltipContent side="top">
              {open ? 'Close model list' : 'Browse models'}
            </TooltipContent>
          </Tooltip>
        </div>

        <PopoverContent
          anchor={anchorRef}
          align="start"
          side="bottom"
          sideOffset={4}
          initialFocus={false}
          finalFocus={false}
          className="w-(--anchor-width) min-w-64 p-1"
        >
          <ScrollArea className="max-h-64" viewportClassName="max-h-64">
            <div
              ref={listRef}
              id={listboxId}
              role="listbox"
              aria-label={ariaLabel ?? 'Models'}
              {...hover.handlers}
              onMouseMove={(event) => {
                hover.handlers.onMouseMove(event)
                const item = (event.target as HTMLElement | null)?.closest?.('[data-fluid-hover-index]')
                const idxAttr = item?.getAttribute('data-fluid-hover-index')
                if (idxAttr !== null && idxAttr !== undefined) {
                  const parsed = Number.parseInt(idxAttr, 10)
                  if (!Number.isNaN(parsed)) setActiveIndex(parsed)
                }
              }}
              className="relative flex flex-col"
            >
              <FluidHoverHighlight hover={hover} className={shape.bg} />

              {rows.length > 0 ? (
                rows.map((row, idx) => (
                  <ModelComboboxRow
                    key={`${row.kind}:${row.id}`}
                    row={row}
                    index={idx}
                    optionId={optionIdFor(idx)}
                    selected={row.kind === 'model' && row.id === value}
                    active={idx === clampedActiveIndex}
                    registerItem={hover.registerItem}
                    onSelect={selectModel}
                  />
                ))
              ) : (
                <div className={cn(typeClass('caption'), 'px-2.5 py-2 text-muted-foreground')}>
                  {loading
                    ? 'Fetching models…'
                    : (emptyHint ?? 'No models listed. Type any custom model ID above.')}
                </div>
              )}
            </div>
          </ScrollArea>

          {loading && rows.length > 0 && (
            <div
              role="status"
              className={cn(
                typeClass('caption'),
                'border-t border-border/60 px-2.5 py-1.5 text-muted-foreground',
              )}
            >
              Refreshing models…
            </div>
          )}
        </PopoverContent>
      </Popover>

      {onRefresh && (
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                type="button"
                variant="outline"
                size="icon"
                disabled={disabled || loading}
                aria-label="Refresh models"
                onClick={onRefresh}
                className="shrink-0"
              />
            }
          >
            <RefreshCw
              className={cn('size-3.5', loading && 'animate-spin motion-reduce:animate-none')}
              aria-hidden="true"
            />
          </TooltipTrigger>
          <TooltipContent side="top">Refresh /models</TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}
