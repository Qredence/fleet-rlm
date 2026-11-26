import { useLayoutEffect, useRef, type ReactNode } from 'react'
import type { UseFluidHoverReturn } from '@/hooks/use-fluid-hover'
import { cn } from '@/lib/utils'

interface HighlightRect {
  left: number
  top: number
  width: number
  height: number
}

export function FluidHoverHighlight({
  hover,
  rect: explicitRect,
  session: explicitSession,
  hidden = false,
  transition = true,
  onFrame,
  children,
  className,
}: {
  hover?: Pick<UseFluidHoverReturn, 'activeIndex' | 'itemRects' | 'isMeasured' | 'session'>
  rect?: HighlightRect | null
  session?: number
  hidden?: boolean
  transition?: boolean | false
  onFrame?: (box: { y: number; height: number; opacity: number }) => void
  children?: ReactNode
  className?: string
}) {
  const fill = useRef<HTMLDivElement>(null)
  const rect =
    explicitRect !== undefined
      ? explicitRect ?? undefined
      : hover && hover.isMeasured && hover.activeIndex !== null
        ? hover.itemRects[hover.activeIndex]
        : undefined
  const session = explicitSession ?? hover?.session ?? 0
  const visible = Boolean(rect) && !hidden

  useLayoutEffect(() => {
    if (rect && fill.current) {
      const { style } = fill.current
      style.transform = `translate(${rect.left}px, ${rect.top}px)`
      style.width = `${rect.width}px`
      style.height = `${rect.height}px`
    }
    onFrame?.(
      rect
        ? { y: rect.top, height: rect.height, opacity: visible ? 1 : 0 }
        : { y: 0, height: 0, opacity: 0 },
    )
  }, [onFrame, rect, session, visible])

  return (
    <div
      key={session}
      ref={fill}
      data-slot="fluid-hover-highlight"
      data-visible={visible || undefined}
      className={cn(
        'pointer-events-none absolute top-0 left-0 bg-hover opacity-0 starting:opacity-0 data-visible:opacity-100 motion-reduce:transition-none',
        transition === false ? 'transition-none' : 'transition-[transform,width,height,opacity] duration-150 ease-out',
        className,
      )}
    >
      {children}
    </div>
  )
}
