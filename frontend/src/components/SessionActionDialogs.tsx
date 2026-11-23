import { useRef } from 'react'
import type { Session } from '@/lib/api'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

export function SessionActionDialogs({ action, finalFocus, closeAction, title, setTitle, changing, actionError, commitAction }: {
  action: { kind: 'rename' | 'delete'; session: Session } | null
  finalFocus: () => HTMLButtonElement | null
  closeAction: (open: boolean) => void
  title: string
  setTitle: (title: string) => void
  changing: boolean
  actionError: string
  commitAction: () => Promise<void>
}) {
  const cancelDelete = useRef<HTMLButtonElement>(null)
  return (
    <>
      <Dialog open={action?.kind === 'rename'} onOpenChange={closeAction}>
        <DialogContent finalFocus={finalFocus}>
          <DialogHeader>
            <DialogTitle>Rename session</DialogTitle>
            <DialogDescription>Choose a title for this conversation.</DialogDescription>
          </DialogHeader>
          <form className="flex flex-col gap-4" onSubmit={(event) => {
            event.preventDefault()
            if (title.trim()) void commitAction()
          }}>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="session-title">Session title</FieldLabel>
                <Input id="session-title" autoFocus maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} />
              </Field>
            </FieldGroup>
            {actionError && <Alert variant="destructive"><AlertDescription>{actionError}</AlertDescription></Alert>}
            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" disabled={changing} />}>Cancel</DialogClose>
              <Button type="submit" disabled={changing || !title.trim()}>Save</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <AlertDialog open={action?.kind === 'delete'} onOpenChange={closeAction}>
        <AlertDialogContent initialFocus={cancelDelete} finalFocus={finalFocus}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{action?.session.title}”?</AlertDialogTitle>
            <AlertDialogDescription>This permanently deletes the conversation and its workspace files.</AlertDialogDescription>
          </AlertDialogHeader>
          {actionError && <Alert variant="destructive"><AlertDescription>{actionError}</AlertDescription></Alert>}
          <AlertDialogFooter>
            <AlertDialogCancel ref={cancelDelete} disabled={changing}>Cancel</AlertDialogCancel>
            <Button variant="destructive" disabled={changing} onClick={() => void commitAction()}>Delete</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
