import { useLayoutEffect, useRef } from 'react'
import type { UseFluidHoverReturn } from '@/hooks/use-fluid-hover'
import { cn } from '@/lib/utils'

export function FluidHoverHighlight({ hover, className }: {
  hover: Pick<UseFluidHoverReturn, 'activeIndex' | 'itemRects' | 'isMeasured' | 'session'>
  className?: string
}) {
  const fill = useRef<HTMLDivElement>(null)
  const { activeIndex, itemRects, isMeasured, session } = hover
  const rect = isMeasured && activeIndex !== null ? itemRects[activeIndex] : undefined

  useLayoutEffect(() => {
    if (!rect || !fill.current) return
    // Keep the last position while the fill fades out. A new pointer session starts a new fill.
    const { style } = fill.current
    style.transform = `translate(${rect.left}px, ${rect.top}px)`
    style.width = `${rect.width}px`
    style.height = `${rect.height}px`
  }, [rect, session])

  return (
    <div
      key={session}
      ref={fill}
      data-slot="fluid-hover-highlight"
      data-visible={Boolean(rect) || undefined}
      className={cn(
        'pointer-events-none absolute top-0 left-0 bg-hover opacity-0 transition-[transform,width,height,opacity] duration-150 ease-out starting:opacity-0 data-visible:opacity-100 motion-reduce:transition-none',
        className,
      )}
    />
  )
}
