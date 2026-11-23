import { lazy, type ComponentProps } from 'react'
import { DeferredOverlay } from '@/components/Deferred'
import type { SheetPanel } from './sheet'

const Sheet = lazy(() => import('./sheet').then((module) => ({ default: module.SheetPanel })))

export function DeferredSheet(props: ComponentProps<typeof SheetPanel>) {
  return <DeferredOverlay open={props.open}><Sheet {...props} /></DeferredOverlay>
}
