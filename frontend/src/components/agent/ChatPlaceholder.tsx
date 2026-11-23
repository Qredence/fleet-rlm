import { Plus, Terminal } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'

export function ChatPlaceholder({ title = 'Chat', showHeader = true, loading, error, actionLabel, onAction, disabled }: {
  title?: string
  showHeader?: boolean
  loading?: boolean
  error?: string
  actionLabel: string
  onAction: () => void
  disabled?: boolean
}) {
  return (
    <>
      {showHeader && <PageHeader title={title} />}
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col p-4 sm:p-6">
        {loading ? (
          <div role="status" aria-label="Loading conversation" className="flex flex-1 flex-col gap-4">
            <Skeleton className="ml-auto h-9 w-2/5 motion-reduce:animate-none" />
            <Skeleton className="h-20 w-4/5 motion-reduce:animate-none" />
            {showHeader && <Skeleton className="mt-auto h-28 w-full motion-reduce:animate-none" />}
          </div>
        ) : (
          <Empty className="flex-1">
            <EmptyHeader>
              <EmptyMedia variant="icon"><Terminal /></EmptyMedia>
              <EmptyTitle>{error ? 'Chat unavailable' : 'Start your first session'}</EmptyTitle>
              <EmptyDescription>{error || 'Create a session to start a conversation.'}</EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button disabled={disabled} onClick={onAction}>
                {!error && <Plus data-icon="inline-start" />}{actionLabel}
              </Button>
            </EmptyContent>
          </Empty>
        )}
      </div>
    </>
  )
}
