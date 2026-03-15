import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { Dialog as BaseDialog } from "@base-ui/react";
import { XIcon } from "lucide-react";

import { cn } from "@/lib/utils/cn";

const Dialog = BaseDialog.Root;
const DialogTrigger = React.forwardRef<any, React.ComponentPropsWithoutRef<typeof BaseDialog.Trigger> & { asChild?: boolean }>(({ asChild, ...props }, ref) => {
  if (asChild) {
    return <BaseDialog.Trigger render={<Slot />} ref={ref} {...props} />;
  }
  return <BaseDialog.Trigger ref={ref} {...props} />;
});
DialogTrigger.displayName = "DialogTrigger";
const DialogPortal = BaseDialog.Portal;
const DialogClose = BaseDialog.Close;

const DialogOverlay = React.forwardRef<
  React.ComponentRef<typeof BaseDialog.Backdrop>,
  React.ComponentPropsWithoutRef<typeof BaseDialog.Backdrop>
>(function DialogOverlay({ className, ...props }, ref) {
  return (
    <BaseDialog.Backdrop
      ref={ref}
      className={cn(
        "fixed inset-0 z-50 bg-black/50 transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0",
        className,
      )}
      {...props}
    />
  );
});
DialogOverlay.displayName = "DialogOverlay";

const DialogContent = React.forwardRef<
  React.ComponentRef<typeof BaseDialog.Popup>,
  React.ComponentPropsWithoutRef<typeof BaseDialog.Popup>
>(function DialogContent({ className, children, ...props }, ref) {
  return (
    <BaseDialog.Portal>
      <DialogOverlay />
      <BaseDialog.Popup
        ref={ref}
        className={cn(
          "bg-background fixed top-[50%] left-[50%] z-50 grid w-full max-w-[calc(100%-2rem)] translate-x-[-50%] translate-y-[-50%] gap-4 rounded-lg border border-border-subtle p-6 shadow-lg duration-200 sm:max-w-lg",
          "transition-all data-[ending-style]:opacity-0 data-[ending-style]:scale-95 data-[starting-style]:opacity-0 data-[starting-style]:scale-95",
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
});
DialogContent.displayName = "DialogContent";

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex flex-col gap-2 text-center sm:text-left", className)}
      {...props}
    />
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

const DialogTitle = React.forwardRef<
  React.ComponentRef<typeof BaseDialog.Title>,
  React.ComponentPropsWithoutRef<typeof BaseDialog.Title>
>(function DialogTitle({ className, ...props }, ref) {
  return (
    <BaseDialog.Title ref={ref} className={cn("text-lg font-semibold leading-none tracking-tight", className)} {...props} />
  );
});
DialogTitle.displayName = "DialogTitle";

const DialogDescription = React.forwardRef<
  React.ComponentRef<typeof BaseDialog.Description>,
  React.ComponentPropsWithoutRef<typeof BaseDialog.Description>
>(function DialogDescription({ className, ...props }, ref) {
  return (
    <BaseDialog.Description ref={ref} className={cn("text-muted-foreground text-sm", className)} {...props} />
  );
});
DialogDescription.displayName = "DialogDescription";

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
