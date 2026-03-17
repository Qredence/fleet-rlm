import type * as React from "react";
import type { VariantProps } from "class-variance-authority";

import { buttonVariants } from "@/components/ui/button-variants";
import { cn } from "@/lib/utils/cn";

// ============================================================================
// InputGroup (container)
// ============================================================================

function InputGroup({
  className,
  ref,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { ref?: React.Ref<HTMLDivElement> }) {
  return (
    <div
      ref={ref}
      data-slot="input-group"
      className={cn(
        "flex items-center rounded-lg border border-border-subtle/70 bg-input-background transition-[border-color,box-shadow] focus-within:border-ring focus-within:ring-ring/50 focus-within:ring-[3px]",
        className,
      )}
      {...props}
    />
  );
}

// ============================================================================
// InputGroupInput
// ============================================================================

function InputGroupInput({
  className,
  ref,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & {
  ref?: React.Ref<HTMLInputElement>;
}) {
  return (
    <input
      ref={ref}
      data-slot="input-group-input"
      className={cn(
        "min-w-0 flex-1 bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

// ============================================================================
// InputGroupTextarea
// ============================================================================

function InputGroupTextarea({
  className,
  ref,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  ref?: React.Ref<HTMLTextAreaElement>;
}) {
  return (
    <textarea
      ref={ref}
      data-slot="input-group-textarea"
      className={cn(
        "min-h-16 min-w-0 w-full flex-1 field-sizing-content resize-none bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

// ============================================================================
// InputGroupAddon
// ============================================================================

function InputGroupAddon({
  className,
  align = "inline-start",
  ref,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  align?: "block-start" | "block-end" | "inline-start" | "inline-end";
  ref?: React.Ref<HTMLDivElement>;
}) {
  const alignClasses = {
    "block-start": "items-start",
    "block-end": "items-end",
    "inline-start": "items-center justify-start",
    "inline-end": "items-center justify-end",
  };

  return (
    <div
      ref={ref}
      data-slot="input-group-addon"
      data-align={align}
      className={cn("flex shrink-0", alignClasses[align], className)}
      {...props}
    />
  );
}

// ============================================================================
// InputGroupText
// ============================================================================

function InputGroupText({
  className,
  ref,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  ref?: React.Ref<HTMLSpanElement>;
}) {
  return (
    <span
      ref={ref}
      data-slot="input-group-text"
      className={cn("px-3 py-2 text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

// ============================================================================
// InputGroupButton
// ============================================================================

type InputGroupButtonVariantProps = VariantProps<typeof buttonVariants>;

function InputGroupButton({
  className,
  variant = "ghost",
  size = "icon-sm",
  ref,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> &
  InputGroupButtonVariantProps & {
    asChild?: boolean;
    ref?: React.Ref<HTMLButtonElement>;
  }) {
  return (
    <button
      ref={ref}
      data-slot="input-group-button"
      data-variant={variant}
      data-size={size}
      className={cn(
        buttonVariants({ variant, size }),
        "shrink-0 rounded-none first:rounded-l-md last:rounded-r-md",
        className,
      )}
      {...props}
    />
  );
}

// ============================================================================
// Exports
// ============================================================================

export {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  InputGroupText,
  InputGroupTextarea,
};
