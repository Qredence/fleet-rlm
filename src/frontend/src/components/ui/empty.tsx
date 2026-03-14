import * as React from "react";

import { cn } from "@/lib/utils/cn";

function Empty({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="empty"
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-subtle/80 bg-muted/15 px-6 py-8 text-center",
        className,
      )}
      {...props}
    />
  );
}

function EmptyMedia({
  className,
  variant = "icon",
  ...props
}: React.ComponentProps<"div"> & {
  variant?: "icon" | "visual";
}) {
  return (
    <div
      data-slot="empty-media"
      data-variant={variant}
      className={cn(
        "flex items-center justify-center rounded-full border border-border-subtle/80 bg-background/80 text-muted-foreground",
        variant === "icon" ? "size-12" : "min-h-12 min-w-12 px-4 py-3",
        className,
      )}
      {...props}
    />
  );
}

function EmptyTitle({ className, ...props }: React.ComponentProps<"h3">) {
  return (
    <h3
      data-slot="empty-title"
      className={cn("text-sm font-medium text-foreground", className)}
      {...props}
    />
  );
}

function EmptyDescription({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="empty-description"
      className={cn("max-w-prose text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

function EmptyContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="empty-content"
      className={cn("flex flex-col items-center gap-3", className)}
      {...props}
    />
  );
}

export { Empty, EmptyContent, EmptyDescription, EmptyMedia, EmptyTitle };
