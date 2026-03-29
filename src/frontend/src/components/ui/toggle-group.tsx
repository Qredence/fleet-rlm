import * as React from "react";
import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group";
import { type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";
import { toggleVariants } from "@/components/ui/toggle-variants";

type ToggleGroupVariant = NonNullable<VariantProps<typeof toggleVariants>["variant"]> | "card";

const ToggleGroupContext = React.createContext<{
  size?: VariantProps<typeof toggleVariants>["size"];
  variant?: ToggleGroupVariant;
}>({
  size: "default",
  variant: "default",
});

function ToggleGroup({
  className,
  variant,
  size,
  children,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Root> &
  Omit<VariantProps<typeof toggleVariants>, "variant"> & {
    variant?: ToggleGroupVariant;
  }) {
  return (
    <ToggleGroupPrimitive.Root
      data-slot="toggle-group"
      data-variant={variant}
      data-size={size}
      className={cn(
        variant === "card"
          ? "group/toggle-group flex w-full flex-wrap items-stretch gap-2"
          : "group/toggle-group flex w-fit items-center rounded-md data-[variant=outline]:shadow-xs",
        className,
      )}
      {...props}
    >
      <ToggleGroupContext.Provider value={{ variant, size }}>
        {children}
      </ToggleGroupContext.Provider>
    </ToggleGroupPrimitive.Root>
  );
}

function ToggleGroupItem({
  className,
  children,
  variant,
  size,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Item> &
  Omit<VariantProps<typeof toggleVariants>, "variant"> & {
    variant?: ToggleGroupVariant;
  }) {
  const context = React.useContext(ToggleGroupContext);
  const resolvedVariant = context.variant || variant;
  const resolvedSize = context.size || size;

  return (
    <ToggleGroupPrimitive.Item
      data-slot="toggle-group-item"
      data-variant={resolvedVariant}
      data-size={resolvedSize}
      className={cn(
        resolvedVariant === "card"
          ? "inline-flex min-w-0 items-start justify-start gap-3 rounded-xl border border-border-subtle bg-background px-3 py-3 text-left whitespace-normal shadow-none transition-[color,box-shadow,background-color,border-color] hover:bg-muted/40 focus:z-10 focus-visible:z-10 data-[state=on]:border-primary/35 data-[state=on]:bg-accent/40 data-[state=on]:text-foreground disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4"
          : cn(
              toggleVariants({
                size: resolvedSize,
                variant: resolvedVariant as VariantProps<typeof toggleVariants>["variant"],
              }),
              "min-w-0 flex-1 shrink-0 rounded-none shadow-none first:rounded-l-md last:rounded-r-md focus:z-10 focus-visible:z-10 data-[variant=outline]:border-l-0 data-[variant=outline]:first:border-l",
            ),
        className,
      )}
      {...props}
    >
      {children}
    </ToggleGroupPrimitive.Item>
  );
}

export { ToggleGroup, ToggleGroupItem };
