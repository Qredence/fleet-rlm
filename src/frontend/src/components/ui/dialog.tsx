import type * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { Dialog as BaseDialog } from "@base-ui/react";
import { XIcon } from "lucide-react";

import { cn } from "@/lib/utils/cn";

const Dialog = BaseDialog.Root;

function DialogTrigger({
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

const DialogPortal = BaseDialog.Portal;
const DialogClose = BaseDialog.Close;

function DialogOverlay({
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
        "fixed inset-0 z-50 bg-black/50 transition-opacity duration-200 data-ending-style:opacity-0 data-starting-style:opacity-0",
        className,
      )}
      {...props}
    />
  );
}

function DialogContent({
  className,
  children,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseDialog.Popup> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseDialog.Popup>>;
}) {
  return (
    <BaseDialog.Portal>
      <DialogOverlay />
      <BaseDialog.Popup
        ref={ref}
        className={cn(
          "bg-background fixed top-1/2 left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 gap-4 rounded-lg border border-border-subtle p-6 shadow-lg duration-200 sm:max-w-lg",
          "transition-all data-ending-style:opacity-0 data-ending-style:scale-95 data-starting-style:opacity-0 data-starting-style:scale-95",
          className,
        )}
        {...props}
      >
        {children}
        <DialogClose className="ring-offset-background focus:ring-ring hover:bg-accent hover:text-accent-foreground absolute top-4 right-4 rounded-xs opacity-70 transition-opacity hover:opacity-100 focus:ring-2 focus:ring-offset-2 focus:outline-hidden disabled:pointer-events-none [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4">
          <XIcon />
          <span className="sr-only">Close</span>
        </DialogClose>
      </BaseDialog.Popup>
    </BaseDialog.Portal>
  );
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("flex flex-col gap-2 text-center sm:text-left", className)} {...props} />
  );
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)}
      {...props}
    />
  );
}

function DialogTitle({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseDialog.Title> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseDialog.Title>>;
}) {
  return (
    <BaseDialog.Title
      ref={ref}
      className={cn("text-lg font-medium leading-none tracking-tight", className)}
      {...props}
    />
  );
}

function DialogDescription({
  className,
  ref,
  ...props
}: React.ComponentPropsWithoutRef<typeof BaseDialog.Description> & {
  ref?: React.Ref<React.ComponentRef<typeof BaseDialog.Description>>;
}) {
  return (
    <BaseDialog.Description
      ref={ref}
      className={cn("text-muted-foreground text-sm", className)}
      {...props}
    />
  );
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
};
