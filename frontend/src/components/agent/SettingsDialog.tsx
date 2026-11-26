import { lazy } from 'react'
import { Deferred } from '@/components/Deferred'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
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
      <DialogContent className="flex h-[min(640px,calc(100dvh-4rem))] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[880px]">
        <DialogTitle className="sr-only">Settings</DialogTitle>
        <DialogDescription className="sr-only">
          Manage workspace appearance, providers, model defaults, and execution settings.
        </DialogDescription>
        {open && (
          <Deferred fallback={<Skeleton role="status" aria-label="Loading settings" className="m-5 h-40 motion-reduce:animate-none" />}>
            <SettingsDialogBody {...props} />
          </Deferred>
        )}
      </DialogContent>
    </Dialog>
  )
}
