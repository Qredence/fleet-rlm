import * as React from "react";
import { Tabs as BaseTabs } from "@base-ui/react";

import { cn } from "@/lib/utils";

type TabsListVariant = "segmented" | "surface" | "underline";

const TabsStyleContext = React.createContext<{
  variant: TabsListVariant;
}>({
  variant: "segmented",
});

function Tabs({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.Root> & {
  ref?: React.ComponentPropsWithRef<typeof BaseTabs.Root>["ref"];
}) {
  return <BaseTabs.Root ref={ref} data-slot="tabs" className={className} {...props} />;
}

function TabsList({
  className,
  variant = "segmented",
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.List> & {
  variant?: TabsListVariant;
  ref?: React.ComponentPropsWithRef<typeof BaseTabs.List>["ref"];
}) {
  return (
    <TabsStyleContext.Provider value={{ variant }}>
      <BaseTabs.List
        ref={ref}
        data-slot="tabs-list"
        data-variant={variant}
        className={cn(
          variant === "underline"
            ? "inline-flex h-10 items-center gap-0 rounded-none border-0 bg-transparent p-0"
            : variant === "surface"
              ? "inline-flex h-10 items-center justify-center rounded-lg border border-border-subtle/70 bg-muted/40 p-1 text-muted-foreground"
              : "inline-flex h-10 items-center justify-center rounded-md bg-muted p-1 text-muted-foreground",
          className,
        )}
        {...props}
      />
    </TabsStyleContext.Provider>
  );
}

function TabsTrigger({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseTabs.Tab> & {
  ref?: React.ComponentPropsWithRef<typeof BaseTabs.Tab>["ref"];
}) {
  const { variant } = React.useContext(TabsStyleContext);

  return (
    <BaseTabs.Tab
      ref={ref}
      data-slot="tabs-trigger"
      data-variant={variant}
      className={cn(
        variant === "underline"
          ? "inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-transparent px-3 pb-2 pt-2.5 text-xs font-medium text-muted-foreground ring-offset-background transition-[color,border-color,box-shadow] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[active]:border-foreground data-[active]:bg-transparent data-[active]:text-foreground data-[active]:shadow-none"
          : "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[active]:bg-background data-[active]:text-foreground data-[active]:shadow-sm",
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
      className={cn(
        "mt-2 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        className,
      )}
      keepMounted
      {...props}
    />
  );
}

export { Tabs, TabsList, TabsTrigger, TabsContent };
