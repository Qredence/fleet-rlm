import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Tabs as BaseTabs } from "@base-ui/react";

import { cn } from "@/lib/utils";

function Tabs({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.Root> & {
  ref?: React.ComponentPropsWithRef<typeof BaseTabs.Root>["ref"];
}) {
  return (
    <BaseTabs.Root
      ref={ref}
      data-slot="tabs"
      className={cn("group/tabs flex gap-2 flex-col", className)}
      {...props}
    />
  );
}

const tabsListVariants = cva(
  "group/tabs-list inline-flex w-fit items-center justify-center rounded-lg p-[3px] text-muted-foreground h-9 data-[variant=line]:rounded-none",
  {
    variants: {
      variant: {
        default: "bg-muted",
        line: "gap-1 bg-transparent",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function TabsList({
  className,
  variant = "default",
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.List> &
  VariantProps<typeof tabsListVariants> & {
    ref?: React.ComponentPropsWithRef<typeof BaseTabs.List>["ref"];
  }) {
  return (
    <BaseTabs.List
      ref={ref}
      data-slot="tabs-list"
      data-variant={variant}
      className={cn(tabsListVariants({ variant }), className)}
      {...props}
    />
  );
}

function TabsTrigger({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.Tab> & {
  ref?: React.ComponentPropsWithRef<typeof BaseTabs.Tab>["ref"];
}) {
  return (
    <BaseTabs.Tab
      ref={ref}
      data-slot="tabs-trigger"
      className={cn(
        "relative inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 rounded-md border border-transparent px-2 py-1 text-sm font-medium whitespace-nowrap text-foreground/60 transition-all hover:text-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-1 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50 dark:text-muted-foreground dark:hover:text-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        "group-data-[variant=line]/tabs-list:bg-transparent group-data-[variant=line]/tabs-list:data-[active]:bg-transparent dark:group-data-[variant=line]/tabs-list:data-[active]:border-transparent dark:group-data-[variant=line]/tabs-list:data-[active]:bg-transparent",
        "data-[active]:bg-background data-[active]:text-foreground dark:data-[active]:border-input dark:data-[active]:bg-input/30 dark:data-[active]:text-foreground",
        "group-data-[variant=default]/tabs-list:data-[active]:shadow-sm group-data-[variant=line]/tabs-list:data-[active]:shadow-none",
        "after:absolute after:bg-foreground after:opacity-0 after:transition-opacity after:inset-x-0 after:bottom-[-5px] after:h-0.5 group-data-[variant=line]/tabs-list:data-[active]:after:opacity-100",
        className,
      )}
      {...props}
    />
  );
}

function TabsContent({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.Panel> & {
  ref?: React.ComponentPropsWithRef<typeof BaseTabs.Panel>["ref"];
}) {
  return (
    <BaseTabs.Panel
      ref={ref}
      data-slot="tabs-content"
      className={cn("w-full flex-1 outline-none", className)}
      keepMounted
      {...props}
    />
  );
}

export { Tabs, TabsList, TabsTrigger, TabsContent, tabsListVariants };
