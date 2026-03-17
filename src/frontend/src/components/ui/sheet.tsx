import type * as React from "react";
import { Dialog as BaseDialog } from "@base-ui/react";
import { XIcon } from "lucide-react";
import { Slot } from "@radix-ui/react-slot";

import { cn } from "@/lib/utils/cn";

const Sheet = BaseDialog.Root;

function SheetTrigger({
  asChild,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseDialog.Trigger> & {
  asChild?: boolean;
  ref?: React.ComponentPropsWithRef<typeof BaseDialog.Trigger>["ref"];
}) {
  if (asChild) {
    return <BaseDialog.Trigger render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseDialog.Trigger ref={ref} {...props} />;
}

const SheetClose = BaseDialog.Close;

function SheetOverlay({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseDialog.Backdrop> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseDialog.Backdrop>>;
}) {
  return (
    <BaseDialog.Backdrop
      ref={ref}
      className={cn(
        "fixed inset-0 z-50 bg-black/50 transition-opacity duration-300 data-ending-style:opacity-0 data-starting-style:opacity-0",
        className,
      )}
      {...props}
    />
  );
}

function SheetContent({
  className,
  children,
  side = "right",
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseDialog.Popup> & {
  side?: "top" | "right" | "bottom" | "left";
  ref?: React.Ref<React.ComponentRef<typeof BaseDialog.Popup>>;
}) {
  return (
    <BaseDialog.Portal>
      <SheetOverlay />
      <BaseDialog.Popup
        ref={ref}
        className={cn(
          "bg-background fixed z-50 flex flex-col gap-4 shadow-lg transition-transform duration-300 ease-in-out",
          side === "right" &&
            "inset-y-0 right-0 h-full w-3/4 border-l sm:max-w-sm data-ending-style:translate-x-full data-starting-style:translate-x-full",
          side === "left" &&
            "inset-y-0 left-0 h-full w-3/4 border-r sm:max-w-sm data-ending-style:-translate-x-full data-starting-style:-translate-x-full",
          side === "top" &&
            "inset-x-0 top-0 h-auto border-b data-ending-style:-translate-y-full data-starting-style:-translate-y-full",
          side === "bottom" &&
            "inset-x-0 bottom-0 h-auto border-t data-ending-style:translate-y-full data-starting-style:translate-y-full",
          className,
        )}
        {...props}
      >
        {children}
        <SheetClose className="ring-offset-background focus:ring-ring hover:bg-secondary absolute top-4 right-4 rounded-xs opacity-70 transition-opacity hover:opacity-100 focus:ring-2 focus:ring-offset-2 focus:outline-hidden disabled:pointer-events-none">
          <XIcon className="size-5" strokeWidth={1.5} />
          <span className="sr-only">Close</span>
        </SheetClose>
      </BaseDialog.Popup>
    </BaseDialog.Portal>
  );
}

function SheetHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-1.5 p-4", className)} {...props} />;
}

function SheetFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("mt-auto flex flex-col gap-2 p-4", className)} {...props} />;
}

const SheetTitle = BaseDialog.Title;
const SheetDescription = BaseDialog.Description;

export {
  Sheet,
  SheetTrigger,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetFooter,
  SheetTitle,
  SheetDescription,
};
