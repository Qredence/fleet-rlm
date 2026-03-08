import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import type { VariantProps } from "class-variance-authority";

import { buttonVariants } from "@/components/ui/button-variants";
import { cn } from "@/lib/utils/cn";

const Button = React.forwardRef<
  HTMLButtonElement,
  React.ComponentPropsWithoutRef<"button"> &
    VariantProps<typeof buttonVariants> & {
      asChild?: boolean;
    }
>(function Button(
  { className, variant, size, asChild = false, ...props },
  ref,
) {
  const Comp = asChild ? Slot : "button";
  const resolvedVariant = variant ?? "default";
  const resolvedSize = size ?? "default";

  return (
    <Comp
      ref={ref}
      data-slot="button"
      data-variant={resolvedVariant}
      data-size={resolvedSize}
      className={cn(
        buttonVariants({
          variant: resolvedVariant,
          size: resolvedSize,
          className,
        }),
      )}
      {...props}
    />
  );
});
Button.displayName = "Button";

export { Button };
