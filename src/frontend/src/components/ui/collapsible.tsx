import * as React from "react";
import { Collapsible as BaseCollapsible } from "@base-ui/react";
import { Slot } from "@radix-ui/react-slot";

const Collapsible = React.forwardRef<
  any,
  React.ComponentPropsWithoutRef<typeof BaseCollapsible.Root> & { asChild?: boolean }
>(({ asChild, ...props }, ref) => {
  if (asChild) {
    return <BaseCollapsible.Root render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseCollapsible.Root ref={ref} {...props} />;
});
Collapsible.displayName = "Collapsible";

const CollapsibleTrigger = React.forwardRef<
  any,
  React.ComponentPropsWithoutRef<typeof BaseCollapsible.Trigger> & { asChild?: boolean }
>(({ asChild, ...props }, ref) => {
  if (asChild) {
    return <BaseCollapsible.Trigger render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseCollapsible.Trigger ref={ref} {...props} />;
});
CollapsibleTrigger.displayName = "CollapsibleTrigger";

const CollapsibleContent = React.forwardRef<
  any,
  React.ComponentPropsWithoutRef<typeof BaseCollapsible.Panel> & { asChild?: boolean }
>(({ asChild, ...props }, ref) => {
  if (asChild) {
    return <BaseCollapsible.Panel render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseCollapsible.Panel ref={ref} {...props} />;
});
CollapsibleContent.displayName = "CollapsibleContent";

export { Collapsible, CollapsibleTrigger, CollapsibleContent };
