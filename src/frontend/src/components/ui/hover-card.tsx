import * as React from "react";
import { PreviewCard as HoverCardPrimitive } from "@base-ui/react/preview-card";

import { withAsChild } from "@/lib/base-ui/as-child";
import { cn } from "@/lib/utils/cn";

const HoverCardDelayContext = React.createContext<{
  closeDelay?: number;
  openDelay?: number;
}>({});

function HoverCard({
  openDelay,
  closeDelay,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Root> & {
  openDelay?: number;
  closeDelay?: number;
}) {
  return (
    <HoverCardDelayContext.Provider value={{ closeDelay, openDelay }}>
      <HoverCardPrimitive.Root data-slot="hover-card" {...props} />
    </HoverCardDelayContext.Provider>
  );
}

function HoverCardTrigger({
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Trigger> & { asChild?: boolean }) {
  const { closeDelay, openDelay } = React.useContext(HoverCardDelayContext);
  const { children, props: triggerProps, render } = withAsChild(props);
  return (
    <HoverCardPrimitive.Trigger
      closeDelay={triggerProps.closeDelay ?? closeDelay}
      data-slot="hover-card-trigger"
      delay={triggerProps.delay ?? openDelay}
      render={render}
      {...triggerProps}
    >
      {children}
    </HoverCardPrimitive.Trigger>
  );
}

function HoverCardContent({
  className,
  align = "center",
  sideOffset = 4,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Popup> & {
  align?: "center" | "end" | "start";
  sideOffset?: number;
}) {
  return (
    <HoverCardPrimitive.Portal data-slot="hover-card-portal">
      <HoverCardPrimitive.Positioner align={align} sideOffset={sideOffset}>
        <HoverCardPrimitive.Popup
          data-slot="hover-card-content"
          className={cn(
            "bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 w-64 origin-(--transform-origin) rounded-md border border-border-subtle p-4 shadow-md outline-hidden",
            className,
          )}
          {...props}
        />
      </HoverCardPrimitive.Positioner>
    </HoverCardPrimitive.Portal>
  );
}

export { HoverCard, HoverCardTrigger, HoverCardContent };
