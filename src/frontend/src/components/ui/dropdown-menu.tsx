import type * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { Menu as BaseMenu } from "@base-ui/react";
import { CheckIcon, ChevronRightIcon, CircleIcon } from "lucide-react";

import { cn } from "@/lib/utils";

const DropdownMenu = BaseMenu.Root;
const DropdownMenuPortal = BaseMenu.Portal;

function DropdownMenuTrigger({
  asChild,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.Trigger> & {
  asChild?: boolean;
  ref?: React.ComponentPropsWithRef<typeof BaseMenu.Trigger>["ref"];
}) {
  if (asChild) {
    return <BaseMenu.Trigger render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseMenu.Trigger ref={ref} {...props} />;
}

function DropdownMenuContent({
  className,
  sideOffset = 4,
  alignOffset,
  align,
  side,
  forceMount: _forceMount,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.Popup> & {
  sideOffset?: number;
  alignOffset?: number;
  align?: "start" | "center" | "end";
  side?: "top" | "right" | "bottom" | "left";
  forceMount?: boolean;
  ref?: React.Ref<React.ComponentRef<typeof BaseMenu.Popup>>;
}) {
  return (
    <BaseMenu.Portal>
      <BaseMenu.Positioner
        sideOffset={sideOffset}
        alignOffset={alignOffset}
        align={align}
        side={side}
      >
        <BaseMenu.Popup
          ref={ref}
          className={cn(
            "bg-popover text-popover-foreground z-50 min-w-32 overflow-x-hidden overflow-y-auto rounded-md border border-border-subtle p-1 shadow-md",
            "transition-[opacity,transform] data-ending-style:opacity-0 data-ending-style:scale-95 data-starting-style:opacity-0 data-starting-style:scale-95",
            className,
          )}
          {...props}
        />
      </BaseMenu.Positioner>
    </BaseMenu.Portal>
  );
}

const DropdownMenuGroup = BaseMenu.Group;

function DropdownMenuItem({
  className,
  inset,
  variant = "default",
  asChild,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.Item> & {
  inset?: boolean;
  variant?: "default" | "destructive";
  asChild?: boolean;
  ref?: React.Ref<React.ComponentRef<typeof BaseMenu.Item>>;
}) {
  if (asChild) {
    return (
      <BaseMenu.Item
        ref={ref}
        data-inset={inset}
        data-variant={variant}
        className={cn(
          "focus:bg-muted data-highlighted:bg-muted relative flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 outline-hidden select-none data-disabled:pointer-events-none data-disabled:opacity-50 data-inset:pl-8",
          className,
        )}
        render={<Slot />}
        {...props}
      />
    );
  }
  return (
    <BaseMenu.Item
      ref={ref}
      data-inset={inset}
      data-variant={variant}
      className={cn(
        "focus:bg-muted data-highlighted:bg-muted data-[variant=destructive]:text-destructive data-[variant=destructive]:data-highlighted:bg-destructive/10 dark:data-[variant=destructive]:data-highlighted:bg-destructive/20 data-[variant=destructive]:data-highlighted:text-destructive data-[variant=destructive]:*:[svg]:text-destructive! [&_svg:not([class*='text-'])]:text-muted-foreground relative flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 outline-hidden select-none data-disabled:pointer-events-none data-disabled:opacity-50 data-inset:pl-8 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    />
  );
}

function DropdownMenuCheckboxItem({
  className,
  children,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.CheckboxItem> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseMenu.CheckboxItem>>;
}) {
  return (
    <BaseMenu.CheckboxItem
      ref={ref}
      className={cn(
        "focus:bg-muted data-highlighted:bg-muted relative flex cursor-default items-center gap-2 rounded-sm py-1.5 pr-2 pl-8 outline-hidden select-none data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    >
      <span className="pointer-events-none absolute left-2 flex size-3.5 items-center justify-center">
        <BaseMenu.CheckboxItemIndicator>
          <CheckIcon className="size-4" />
        </BaseMenu.CheckboxItemIndicator>
      </span>
      {children}
    </BaseMenu.CheckboxItem>
  );
}

const DropdownMenuRadioGroup = BaseMenu.RadioGroup;

function DropdownMenuRadioItem({
  className,
  children,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.RadioItem> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseMenu.RadioItem>>;
}) {
  return (
    <BaseMenu.RadioItem
      ref={ref}
      className={cn(
        "focus:bg-muted data-highlighted:bg-muted relative flex cursor-default items-center gap-2 rounded-sm py-1.5 pr-2 pl-8 outline-hidden select-none data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    >
      <span className="pointer-events-none absolute left-2 flex size-3.5 items-center justify-center">
        <BaseMenu.RadioItemIndicator>
          <CircleIcon className="size-2 fill-current" />
        </BaseMenu.RadioItemIndicator>
      </span>
      {children}
    </BaseMenu.RadioItem>
  );
}

function DropdownMenuLabel({
  className,
  inset,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.GroupLabel> & {
  inset?: boolean;
  ref?: React.Ref<React.ComponentRef<typeof BaseMenu.GroupLabel>>;
}) {
  return (
    <BaseMenu.GroupLabel
      ref={ref}
      data-inset={inset}
      className={cn("px-2 py-1.5 data-inset:pl-8", className)}
      {...props}
    />
  );
}

function DropdownMenuSeparator({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.Separator> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseMenu.Separator>>;
}) {
  return (
    <BaseMenu.Separator
      ref={ref}
      className={cn("bg-border -mx-1 my-1 h-px", className)}
      {...props}
    />
  );
}

function DropdownMenuShortcut({
  className,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <span
      className={cn("text-muted-foreground ml-auto tracking-widest", className)}
      {...props}
    />
  );
}

const DropdownMenuSub = BaseMenu.Root;

function DropdownMenuSubTrigger({
  className,
  inset,
  children,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.Trigger> & {
  inset?: boolean;
  ref?: React.ComponentPropsWithRef<typeof BaseMenu.Trigger>["ref"];
}) {
  return (
    <BaseMenu.Trigger
      ref={ref}
      data-inset={inset}
      className={cn(
        "focus:bg-muted data-highlighted:bg-muted flex cursor-default items-center rounded-sm px-2 py-1.5 outline-hidden select-none data-inset:pl-8",
        className,
      )}
      {...props}
    >
      {children}
      <ChevronRightIcon className="ml-auto size-4" />
    </BaseMenu.Trigger>
  );
}

function DropdownMenuSubContent({
  className,
  forceMount: _forceMount,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseMenu.Popup> & {
  forceMount?: boolean;
  ref?: React.Ref<React.ComponentRef<typeof BaseMenu.Popup>>;
}) {
  return (
    <BaseMenu.Portal>
      <BaseMenu.Positioner>
        <BaseMenu.Popup
          ref={ref}
          className={cn(
            "bg-popover text-popover-foreground z-50 min-w-32 overflow-x-hidden overflow-y-auto rounded-md border border-border-subtle p-1 shadow-md",
            "transition-[opacity,transform] data-ending-style:opacity-0 data-ending-style:scale-95 data-starting-style:opacity-0 data-starting-style:scale-95",
            className,
          )}
          {...props}
        />
      </BaseMenu.Positioner>
    </BaseMenu.Portal>
  );
}

export {
  DropdownMenu,
  DropdownMenuPortal,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuItem,
  DropdownMenuCheckboxItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
};
