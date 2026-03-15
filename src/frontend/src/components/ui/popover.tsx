import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { Popover as BasePopover } from "@base-ui/react";

import { cn } from "@/lib/utils/cn";

const Popover = BasePopover.Root;
const PopoverTrigger = React.forwardRef<any, React.ComponentPropsWithoutRef<typeof BasePopover.Trigger> & { asChild?: boolean }>(({ asChild, ...props }, ref) => {
  if (asChild) {
    return <BasePopover.Trigger render={<Slot />} ref={ref} {...props} />;
  }
  return <BasePopover.Trigger ref={ref} {...props} />;
});
PopoverTrigger.displayName = "PopoverTrigger";

const PopoverContent = React.forwardRef<
  React.ComponentRef<typeof BasePopover.Popup>,
  React.ComponentPropsWithoutRef<typeof BasePopover.Popup> & {
    align?: "start" | "center" | "end";
    side?: "top" | "right" | "bottom" | "left";
    sideOffset?: number;
    alignOffset?: number;
    forceMount?: boolean;
  }
>(function PopoverContent({ className, align = "center", side, alignOffset, sideOffset = 4, forceMount, ...props }, ref) {
  return (
    <BasePopover.Portal>
      <BasePopover.Positioner sideOffset={sideOffset} alignOffset={alignOffset} align={align} side={side}>
        <BasePopover.Popup
          ref={ref}
          className={cn(
            "bg-popover text-popover-foreground z-50 w-72 rounded-md border border-border-subtle p-4 shadow-md outline-hidden",
            "transition-all data-[ending-style]:opacity-0 data-[ending-style]:scale-95 data-[starting-style]:opacity-0 data-[starting-style]:scale-95",
            className,
          )}
          {...props}
        />
      </BasePopover.Positioner>
    </BasePopover.Portal>
  );
});
PopoverContent.displayName = "PopoverContent";

const PopoverAnchor = BasePopover.Positioner;

export { Popover, PopoverTrigger, PopoverContent, PopoverAnchor };
