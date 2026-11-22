import type { ReactNode, RefObject } from 'react'
import { cn } from 'cn'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'

export interface PanelPresentation {
  inline: boolean
  open: boolean
  onClose: () => void
  triggerRef: RefObject<HTMLButtonElement | null>
}

export function ResponsivePanel({
  inline, open, onClose, triggerRef, side, title, children,
}: PanelPresentation & {
  side: 'left' | 'right'
  title: string
  children: ReactNode
}) {
  if (inline) {
    return (
      <aside
        aria-label={title}
        className={cn(
          'min-h-0 shrink-0 flex-col bg-sidebar text-sidebar-foreground',
          side === 'left' ? 'w-64 border-r border-sidebar-border' : 'w-80 border-l border-sidebar-border',
          open ? 'flex' : 'hidden',
        )}
      >
        {children}
      </aside>
    )
  }

  return (
    <Sheet open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <SheetContent side={side} finalFocus={triggerRef} className="gap-0">
        <SheetHeader className="shrink-0 border-b pr-12">
          <SheetTitle>{title}</SheetTitle>
        </SheetHeader>
        {children}
      </SheetContent>
    </Sheet>
  )
}
