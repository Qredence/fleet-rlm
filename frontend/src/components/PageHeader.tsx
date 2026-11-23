import type { ReactNode } from 'react'
import { SidebarTrigger, useSidebar } from '@/components/ui/sidebar'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

export function PaneHeader({ children }: { children: ReactNode }) {
  return <header className="flex h-(--pane-header-height) shrink-0 items-center gap-2 border-b px-2 sm:px-4">{children}</header>
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  const { open, openMobile, isMobile } = useSidebar()
  return (
    <PaneHeader>
      <Tooltip>
        <TooltipTrigger render={<SidebarTrigger
          id="fleet-sidebar-trigger" aria-label="Toggle navigation sidebar" aria-expanded={isMobile ? openMobile : open}
          aria-haspopup={isMobile ? 'dialog' : undefined}
        />} />
        <TooltipContent>Toggle navigation sidebar</TooltipContent>
      </Tooltip>
      <h1 title={title} className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h1>
      {children}
    </PaneHeader>
  )
}
