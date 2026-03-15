import * as React from "react";
import { Tabs as BaseTabs } from "@base-ui/react";

import { cn } from "@/lib/utils/cn";

const Tabs = BaseTabs.Root;

const TabsList = React.forwardRef<
  React.ComponentRef<typeof BaseTabs.List>,
  React.ComponentPropsWithoutRef<typeof BaseTabs.List>
>(function TabsList({ className, ...props }, ref) {
  return (
    <BaseTabs.List
      ref={ref}
      className={cn(
        "bg-muted text-muted-foreground inline-flex h-9 items-center justify-center rounded-lg p-1",
        className,
      )}
      {...props}
    />
  );
});
TabsList.displayName = "TabsList";

const TabsTrigger = React.forwardRef<
  React.ComponentRef<typeof BaseTabs.Tab>,
  React.ComponentPropsWithoutRef<typeof BaseTabs.Tab>
>(function TabsTrigger({ className, ...props }, ref) {
  return (
    <BaseTabs.Tab
      ref={ref}
      className={cn(
        "ring-offset-background focus-visible:ring-ring data-[selected]:bg-background data-[selected]:text-foreground data-[selected]:shadow-xs inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium transition-all focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-hidden disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
});
TabsTrigger.displayName = "TabsTrigger";

const TabsContent = React.forwardRef<
  React.ComponentRef<typeof BaseTabs.Panel>,
  React.ComponentPropsWithoutRef<typeof BaseTabs.Panel> & { forceMount?: boolean }
>(function TabsContent({ className, forceMount, ...props }, ref) {
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
});
TabsContent.displayName = "TabsContent";

export { Tabs, TabsList, TabsTrigger, TabsContent };
