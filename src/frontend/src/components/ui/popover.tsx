import type * as React from "react";
import { Popover as BasePopover } from "@base-ui/react";

import { cn } from "@/lib/utils";

const Popover = BasePopover.Root;

function PopoverTrigger({
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BasePopover.Trigger> & {
  ref?: React.ComponentPropsWithRef<typeof BasePopover.Trigger>["ref"];
}) {
  return <BasePopover.Trigger ref={ref} {...props} />;
}

function PopoverContent({
  className,
  align = "center",
  side,
  alignOffset,
  sideOffset = 4,
  forceMount: _forceMount,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BasePopover.Popup> & {
  align?: "start" | "center" | "end";
  side?: "top" | "right" | "bottom" | "left";
  sideOffset?: number;
  alignOffset?: number;
  forceMount?: boolean;
  ref?: React.Ref<React.ComponentRef<typeof BasePopover.Popup>>;
}) {
  return (
    <BasePopover.Portal>
      <BasePopover.Positioner
        className="z-50"
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        align={align}
        side={side}
      >
        <BasePopover.Popup
          ref={ref}
          className={cn(
            "bg-popover text-popover-foreground z-50 w-72 rounded-md border border-border-subtle p-4 shadow-md outline-hidden",
            "transition-all data-ending-style:opacity-0 data-ending-style:scale-95 data-starting-style:opacity-0 data-starting-style:scale-95",
            className,
          )}
          {...props}
        />
      </BasePopover.Positioner>
    </BasePopover.Portal>
  );
}

const PopoverAnchor = BasePopover.Positioner;

export { Popover, PopoverTrigger, PopoverContent, PopoverAnchor };
