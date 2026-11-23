import { useEffect, useRef, type ComponentProps, type ReactNode, type RefObject } from 'react'
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator,
} from './dropdown-menu'

interface MenuItem {
  label: string
  icon: ReactNode
  disabled?: boolean
  variant?: 'default' | 'destructive'
  className?: string
  onSelect: () => void
}

interface CheckboxGroup {
  label: string
  options: Array<{
    id: string
    label: string
    description: string
    checked: boolean
    onCheckedChange: (checked: boolean) => void
  }>
}

export type DropdownMenuOptions = Pick<ComponentProps<typeof DropdownMenuContent>, 'align' | 'side' | 'sideOffset' | 'className'> & {
  modal?: boolean
} & (
  | { items: MenuItem[]; checkboxGroup?: CheckboxGroup; choices?: never }
  | { items?: never; checkboxGroup?: never; choices: { value: string; options: Array<MenuItem & { value: string }> } }
)

type DropdownMenuPopupProps = DropdownMenuOptions & {
  anchor: RefObject<HTMLButtonElement | null>
  triggerId: string
  keyboardStart: 'first' | 'last' | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function DropdownMenuPopup({
  anchor, triggerId, keyboardStart, open, onOpenChange, modal, items, choices, checkboxGroup, ...content
}: DropdownMenuPopupProps) {
  const popup = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open || !keyboardStart) return
    // The trigger lives outside the lazy root, so forward its keyboard opening intent once the popup mounts.
    const frame = window.requestAnimationFrame(() => {
      const menu = popup.current
      if (!menu || (document.activeElement !== menu && document.activeElement !== anchor.current)) return
      const options = menu.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"], [data-disabled])')
      const item = keyboardStart === 'first' ? options[0] : options[options.length - 1]
      item?.focus()
    })
    return () => window.cancelAnimationFrame(frame)
  }, [anchor, keyboardStart, open])

  return (
    <DropdownMenu
      open={open}
      modal={modal}
      onOpenChange={(next, { event }) => {
        // The external trigger toggles on click after the primitive handles pointer dismissal.
        if (!next && event.target instanceof Node && anchor.current?.contains(event.target)) return
        onOpenChange(next)
      }}
    >
      <DropdownMenuContent {...content} anchor={anchor} aria-labelledby={triggerId} finalFocus={anchor} render={<div ref={popup} />}>
        {choices ? (
          <DropdownMenuRadioGroup value={choices.value}>
            {choices.options.map(({ label, value, icon, onSelect }) => (
              <DropdownMenuRadioItem key={value} value={value} onClick={onSelect}>{icon}{label}</DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        ) : items.map(({ label, icon, onSelect, ...props }) => (
          <DropdownMenuItem key={label} {...props} onClick={onSelect}>{icon}{label}</DropdownMenuItem>
        ))}
        {checkboxGroup && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuLabel>{checkboxGroup.label}</DropdownMenuLabel>
              {checkboxGroup.options.map(({ id, label, description, checked, onCheckedChange }) => (
                <DropdownMenuCheckboxItem
                  key={id} label={label} checked={checked} onCheckedChange={onCheckedChange} closeOnClick={false}
                  title={description || label} aria-description={description || undefined} className="min-h-9 pl-2"
                >
                  <span className="truncate">{label}</span>
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuGroup>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
