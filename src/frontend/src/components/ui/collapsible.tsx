import type * as React from "react";
import { Collapsible as BaseCollapsible } from "@base-ui/react";
import { Slot } from "@radix-ui/react-slot";

function Collapsible({
  asChild,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseCollapsible.Root> & {
  asChild?: boolean;
  ref?: React.ComponentPropsWithRef<typeof BaseCollapsible.Root>["ref"];
}) {
  if (asChild) {
    return <BaseCollapsible.Root render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseCollapsible.Root ref={ref} {...props} />;
}

function CollapsibleTrigger({
  asChild,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseCollapsible.Trigger> & {
  asChild?: boolean;
  ref?: React.ComponentPropsWithRef<typeof BaseCollapsible.Trigger>["ref"];
}) {
  if (asChild) {
    return <BaseCollapsible.Trigger render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseCollapsible.Trigger ref={ref} {...props} />;
}

function CollapsibleContent({
  asChild,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseCollapsible.Panel> & {
  asChild?: boolean;
  ref?: React.ComponentPropsWithRef<typeof BaseCollapsible.Panel>["ref"];
}) {
  if (asChild) {
    return <BaseCollapsible.Panel render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseCollapsible.Panel ref={ref} {...props} />;
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent };
