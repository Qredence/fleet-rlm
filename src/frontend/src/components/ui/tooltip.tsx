import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { Tooltip as BaseTooltip } from "@base-ui/react";

import { cn } from "@/lib/utils/cn";

// Base UI doesn't strictly need a Provider in the same way Radix does,
// but we keep the export for API compatibility if it was used globally.
const TooltipProvider = ({ children, ...props }: { children: React.ReactNode; [key: string]: any }) => <>{children}</>;

const Tooltip = BaseTooltip.Root;
const TooltipTrigger = React.forwardRef<any, React.ComponentPropsWithoutRef<typeof BaseTooltip.Trigger> & { asChild?: boolean }>(({ asChild, ...props }, ref) => {
  if (asChild) {
    return <BaseTooltip.Trigger render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseTooltip.Trigger ref={ref} {...props} />;
});
TooltipTrigger.displayName = "TooltipTrigger";

const TooltipContent = React.forwardRef<
  React.ComponentRef<typeof BaseTooltip.Popup>,
  React.ComponentPropsWithoutRef<typeof BaseTooltip.Popup> & {
    sideOffset?: number;
    alignOffset?: number;
    align?: "start" | "center" | "end";
    side?: "top" | "right" | "bottom" | "left";
    forceMount?: boolean;
  }
>(function TooltipContent({ className, sideOffset = 4, alignOffset, align, side, forceMount, children, ...props }, ref) {
  return (
    <BaseTooltip.Portal>
      <BaseTooltip.Positioner sideOffset={sideOffset} alignOffset={alignOffset} align={align} side={side}>
        <BaseTooltip.Popup
          ref={ref}
          className={cn(
            "bg-popover text-popover-foreground border border-border-subtle/80 shadow-md z-50 w-fit rounded-md px-3 py-1.5 text-sm text-balance",
            "transition-all data-[ending-style]:opacity-0 data-[ending-style]:scale-95 data-[starting-style]:opacity-0 data-[starting-style]:scale-95",
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
});
TooltipContent.displayName = "TooltipContent";

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
