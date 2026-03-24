import type * as React from "react";
import { Select as BaseSelect } from "@base-ui/react";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { Slot } from "@radix-ui/react-slot";

import { cn } from "@/lib/utils";

const Select = BaseSelect.Root;
const SelectGroup = BaseSelect.Group;
const SelectValue = BaseSelect.Value;
type SelectPositionerProps = React.ComponentPropsWithoutRef<
  typeof BaseSelect.Positioner
>;

function SelectTrigger({
  className,
  size = "default",
  asChild,
  children,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseSelect.Trigger> & {
  size?: "sm" | "default";
  asChild?: boolean;
  ref?: React.Ref<React.ComponentRef<typeof BaseSelect.Trigger>>;
}) {
  if (asChild) {
    return (
      <BaseSelect.Trigger render={<Slot />} ref={ref} {...props}>
        {children}
      </BaseSelect.Trigger>
    );
  }

  return (
    <BaseSelect.Trigger
      ref={ref}
      data-size={size}
      className={cn(
        "border-border-subtle/70 data-placeholder:text-muted-foreground [&_svg:not([class*='text-'])]:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:bg-input/30 hover:bg-muted flex w-full items-center justify-between gap-2 rounded-lg border bg-input-background px-3 py-2 whitespace-nowrap transition-[color,box-shadow,border-color] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 data-[size=default]:h-9 data-[size=sm]:h-8 *:data-[slot=select-value]:line-clamp-1 *:data-[slot=select-value]:flex *:data-[slot=select-value]:items-center *:data-[slot=select-value]:gap-2 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    >
      {children}
      <BaseSelect.Icon>
        <ChevronDownIcon className="size-4 opacity-50" />
      </BaseSelect.Icon>
    </BaseSelect.Trigger>
  );
}

function SelectContent({
  className,
  children,
  position: _position = "popper",
  sideOffset = 4,
  align,
  side,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseSelect.Popup> & {
  position?: "popper" | "item-aligned";
  align?: SelectPositionerProps["align"];
  side?: SelectPositionerProps["side"];
  sideOffset?: number;
  ref?: React.Ref<React.ComponentRef<typeof BaseSelect.Popup>>;
}) {
  return (
    <BaseSelect.Portal>
      <BaseSelect.Positioner sideOffset={sideOffset} align={align} side={side}>
        <BaseSelect.Popup
          ref={ref}
          className={cn(
            "bg-popover text-popover-foreground relative z-50 overflow-x-hidden overflow-y-auto rounded-lg border border-border-subtle shadow-md",
            "transition-all data-ending-style:opacity-0 data-ending-style:scale-95 data-starting-style:opacity-0 data-starting-style:scale-95",
            className,
          )}
          {...props}
        >
          {children}
        </BaseSelect.Popup>
      </BaseSelect.Positioner>
    </BaseSelect.Portal>
  );
}

const SelectLabel = BaseSelect.GroupLabel;

function SelectItem({
  className,
  children,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseSelect.Item> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseSelect.Item>>;
}) {
  return (
    <BaseSelect.Item
      ref={ref}
      className={cn(
        "focus:bg-muted data-highlighted:bg-muted [&_svg:not([class*='text-'])]:text-muted-foreground relative flex w-full cursor-default items-center gap-2 rounded-sm py-1.5 pr-8 pl-2 outline-hidden select-none data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2",
        className,
      )}
      {...props}
    >
      <span className="absolute right-2 flex size-3.5 items-center justify-center">
        <BaseSelect.ItemIndicator>
          <CheckIcon className="size-4" />
        </BaseSelect.ItemIndicator>
      </span>
      <BaseSelect.ItemText>{children}</BaseSelect.ItemText>
    </BaseSelect.Item>
  );
}

function SelectSeparator({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseSelect.Separator> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseSelect.Separator>>;
}) {
  return (
    <BaseSelect.Separator
      ref={ref}
      className={cn("bg-border pointer-events-none -mx-1 my-1 h-px", className)}
      {...props}
    />
  );
}

function SelectScrollUpButton(_props: React.ComponentProps<"div">) {
  return null;
}

function SelectScrollDownButton(_props: React.ComponentProps<"div">) {
  return null;
}

export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectScrollDownButton,
  SelectScrollUpButton,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
};
