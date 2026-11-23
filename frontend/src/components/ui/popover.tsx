import { Popover as PopoverPrimitive } from '@base-ui/react/popover'
import { SurfaceProvider } from '@/lib/surface-context'
import { useSurfaceLayer } from '@/lib/use-surface-layer'
import { cn } from '@/lib/utils'

const Popover = PopoverPrimitive.Root
const PopoverTrigger = PopoverPrimitive.Trigger

function PopoverContent({
  side = 'bottom', align = 'center', sideOffset = 6, anchor, className, children, ...props
}: PopoverPrimitive.Popup.Props & Pick<PopoverPrimitive.Positioner.Props, 'side' | 'align' | 'sideOffset' | 'anchor'>) {
  const { level, surfaceClass } = useSurfaceLayer(2, 3)
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner side={side} align={align} sideOffset={sideOffset} anchor={anchor} className="z-50">
        <PopoverPrimitive.Popup
          data-slot="popover-content"
          data-surface={level}
          className={cn('max-w-(--available-width) rounded-xl text-popover-foreground ring-1 ring-foreground/10 outline-none', surfaceClass, className)}
          {...props}
        >
          <SurfaceProvider value={level}>{children}</SurfaceProvider>
        </PopoverPrimitive.Popup>
      </PopoverPrimitive.Positioner>
    </PopoverPrimitive.Portal>
  )
}

export { Popover, PopoverContent, PopoverTrigger }
