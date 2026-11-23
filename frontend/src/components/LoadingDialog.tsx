import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'

export function LoadingDialog({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        <Skeleton role="status" aria-label={`Loading ${title.toLowerCase()}`} className="h-28 motion-reduce:animate-none" />
      </DialogContent>
    </Dialog>
  )
}
