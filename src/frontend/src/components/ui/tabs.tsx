import type * as React from "react";
import { Tabs as BaseTabs } from "@base-ui/react";

import { cn } from "@/lib/utils/cn";

const Tabs = BaseTabs.Root;

function TabsList({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.List> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseTabs.List>>;
}) {
  return (
    <BaseTabs.List
      ref={ref}
      data-slot="tabs-list"
      className={cn(
        "bg-muted text-muted-foreground inline-flex h-9 items-center justify-center rounded-lg p-1",
        className,
      )}
      {...props}
    />
  );
}

function TabsTrigger({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.Tab> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseTabs.Tab>>;
}) {
  return (
    <BaseTabs.Tab
      ref={ref}
      data-slot="tabs-trigger"
      className={cn(
        "ring-offset-background focus-visible:ring-ring data-selected:bg-background data-selected:text-foreground data-selected:shadow-xs inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium transition-all focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-hidden disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

function TabsContent({
  className,
  forceMount: _forceMount,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.Panel> & {
  forceMount?: boolean;
  ref?: React.Ref<React.ComponentRef<typeof BaseTabs.Panel>>;
}) {
  return (
    <BaseTabs.Panel
      ref={ref}
      className={cn(
        "ring-offset-background focus-visible:ring-ring mt-2 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-hidden",
        className,
      )}
      {...props}
    />
  );
}

export { Tabs, TabsList, TabsTrigger, TabsContent };
