import type * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { Tooltip as BaseTooltip } from "@base-ui/react";

import { cn } from "@/lib/utils";

// Base UI doesn't strictly need a Provider in the same way Radix does,
// but we keep the export for API compatibility if it was used globally.
function TooltipProvider({ children }: { children: React.ReactNode; [key: string]: unknown }) {
  return <>{children}</>;
}

const Tooltip = BaseTooltip.Root;

function TooltipTrigger({
  asChild,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTooltip.Trigger> & {
  asChild?: boolean;
  ref?: React.ComponentPropsWithRef<typeof BaseTooltip.Trigger>["ref"];
}) {
  if (asChild) {
    return <BaseTooltip.Trigger render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseTooltip.Trigger ref={ref} {...props} />;
}

function TooltipContent({
  className,
  sideOffset = 4,
  alignOffset,
  align,
  side,
  forceMount: _forceMount,
  children,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTooltip.Popup> & {
  sideOffset?: number;
  alignOffset?: number;
  align?: "start" | "center" | "end";
  side?: "top" | "right" | "bottom" | "left";
  forceMount?: boolean;
  ref?: React.Ref<React.ComponentRef<typeof BaseTooltip.Popup>>;
}) {
  return (
    <BaseTooltip.Portal>
      <BaseTooltip.Positioner
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        align={align}
        side={side}
      >
        <BaseTooltip.Popup
          ref={ref}
          data-slot="tooltip-content"
          className={cn(
            "bg-popover text-popover-foreground border border-border-subtle/80 shadow-md z-50 w-fit rounded-md px-3 py-1.5 text-sm text-balance",
            "transition-all data-ending-style:opacity-0 data-ending-style:scale-95 data-starting-style:opacity-0 data-starting-style:scale-95",
            className,
          )}
          {...props}
        >
          {children}
          <BaseTooltip.Arrow className="fill-border-subtle/80" />
        </BaseTooltip.Popup>
      </BaseTooltip.Positioner>
    </BaseTooltip.Portal>
  );
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
