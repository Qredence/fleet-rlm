import type * as React from "react";
import { Collapsible as BaseCollapsible } from "@base-ui/react";

function Collapsible({
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseCollapsible.Root> & {
  ref?: React.ComponentPropsWithRef<typeof BaseCollapsible.Root>["ref"];
}) {
  return <BaseCollapsible.Root ref={ref} {...props} />;
}

function CollapsibleTrigger({
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseCollapsible.Trigger> & {
  ref?: React.ComponentPropsWithRef<typeof BaseCollapsible.Trigger>["ref"];
}) {
  return <BaseCollapsible.Trigger ref={ref} {...props} />;
}

function CollapsibleContent({
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseCollapsible.Panel> & {
  ref?: React.ComponentPropsWithRef<typeof BaseCollapsible.Panel>["ref"];
}) {
  return <BaseCollapsible.Panel ref={ref} {...props} />;
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent };
