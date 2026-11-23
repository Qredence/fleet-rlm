import { lazy } from 'react'
import { Deferred } from '@/components/Deferred'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import type { SettingsDialogProps } from './SettingsDialogBody'

const SettingsDialogBody = lazy(() => import('./SettingsDialogBody').then(({ SettingsDialogBody }) => ({ default: SettingsDialogBody })))

export function SettingsDialog({
  open,
  onOpenChange,
  ...props
}: SettingsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85dvh] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <DialogHeader className="shrink-0 border-b px-5 py-4">
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Manage workspace appearance, model defaults, RLM execution limits, and sandbox configuration.
          </DialogDescription>
        </DialogHeader>
        {open && (
          <Deferred fallback={<Skeleton role="status" aria-label="Loading settings" className="m-5 h-40 motion-reduce:animate-none" />}>
            <SettingsDialogBody {...props} />
          </Deferred>
        )}
      </DialogContent>
    </Dialog>
  )
}
