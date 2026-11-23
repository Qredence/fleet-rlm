import { useRender } from '@base-ui/react/use-render'
import { lazy, useId, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactElement } from 'react'
import { DeferredOverlay } from '@/components/Deferred'
import type { DropdownMenuOptions } from './dropdown-menu-popup'

const DropdownMenuPopup = lazy(() => import('./dropdown-menu-popup').then((module) => ({ default: module.DropdownMenuPopup })))

export function DeferredDropdownMenu({ trigger, ...popup }: DropdownMenuOptions & {
  trigger: ReactElement
}) {
  const anchor = useRef<HTMLButtonElement>(null)
  const triggerId = useId()
  const [open, setOpen] = useState(false)
  const [keyboardStart, setKeyboardStart] = useState<'first' | 'last' | null>(null)
  const button = useRender({
    defaultTagName: 'button',
    render: trigger,
    ref: anchor,
    props: {
      id: triggerId,
      type: 'button',
      'aria-haspopup': 'menu',
      'aria-expanded': open,
      onClick: (event: MouseEvent<HTMLButtonElement>) => {
        setKeyboardStart(event.detail === 0 ? 'first' : null)
        setOpen((current) => !current)
      },
      onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault()
          setKeyboardStart(event.key === 'ArrowUp' ? 'last' : 'first')
          setOpen(true)
        } else if (event.key === 'Escape' && open) {
          event.preventDefault()
          setOpen(false)
        }
      },
    },
  })

  return (
    <>
      {button}
      <DeferredOverlay open={open}>
        <DropdownMenuPopup
          {...popup} anchor={anchor} triggerId={triggerId} keyboardStart={keyboardStart}
          open={open} onOpenChange={setOpen}
        />
      </DeferredOverlay>
    </>
  )
}
