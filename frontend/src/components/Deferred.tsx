import { Component, Suspense, useState, type ReactNode } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'

class LoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <Alert variant="destructive" className="m-4 w-auto">
        <AlertDescription className="flex flex-wrap items-center gap-2">
          This view could not load.
          <Button size="sm" variant="outline" onClick={() => window.location.reload()}>Reload app</Button>
        </AlertDescription>
      </Alert>
    )
  }
}

export function Deferred({ fallback, children }: { fallback: ReactNode; children: ReactNode }) {
  return <LoadBoundary><Suspense fallback={fallback}>{children}</Suspense></LoadBoundary>
}

export function DeferredOverlay({ open, children }: { open: boolean; children: ReactNode }) {
  const [opened, setOpened] = useState(open)
  if (open && !opened) setOpened(true)
  // Keep the root mounted after first use so the primitive can animate out and restore focus.
  return opened || open ? <Deferred fallback={null}>{children}</Deferred> : null
}
