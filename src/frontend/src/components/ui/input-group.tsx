import * as React from "react";
import type { VariantProps } from "class-variance-authority";

import { buttonVariants } from "@/components/ui/button-variants";
import { cn } from "@/lib/utils/cn";

// ============================================================================
// InputGroup (container)
// ============================================================================

interface InputGroupProps extends React.HTMLAttributes<HTMLDivElement> {}

const InputGroup = React.forwardRef<HTMLDivElement, InputGroupProps>(
  ({ className, ...props }, ref) => {
    return (
      <div
        ref={ref}
        data-slot="input-group"
        className={cn(
          "flex items-center rounded-md border bg-background",
          className,
        )}
        {...props}
      />
    );
  },
);
InputGroup.displayName = "InputGroup";

// ============================================================================
// InputGroupInput
// ============================================================================

interface InputGroupInputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {}

const InputGroupInput = React.forwardRef<HTMLInputElement, InputGroupInputProps>(
  ({ className, ...props }, ref) => {
    return (
      <input
        ref={ref}
        data-slot="input-group-input"
        className={cn(
          "flex-1 bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground",
          className,
        )}
        {...props}
      />
    );
  },
);
InputGroupInput.displayName = "InputGroupInput";

// ============================================================================
// InputGroupTextarea
// ============================================================================

interface InputGroupTextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {}

const InputGroupTextarea = React.forwardRef<
  HTMLTextAreaElement,
  InputGroupTextareaProps
>(({ className, ...props }, ref) => {
  return (
    <textarea
      ref={ref}
      data-slot="input-group-textarea"
      className={cn(
        "min-h-16 w-full flex-1 resize-none bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
});
InputGroupTextarea.displayName = "InputGroupTextarea";

// ============================================================================
// InputGroupAddon
// ============================================================================

interface InputGroupAddonProps
  extends React.HTMLAttributes<HTMLDivElement> {
  align?: "block-start" | "block-end" | "inline-start" | "inline-end";
}

const InputGroupAddon = React.forwardRef<HTMLDivElement, InputGroupAddonProps>(
  ({ className, align = "inline-start", ...props }, ref) => {
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
  },
);
InputGroupAddon.displayName = "InputGroupAddon";

// ============================================================================
// InputGroupText
// ============================================================================

interface InputGroupTextProps
  extends React.HTMLAttributes<HTMLSpanElement> {}

const InputGroupText = React.forwardRef<HTMLSpanElement, InputGroupTextProps>(
  ({ className, ...props }, ref) => {
    return (
      <span
        ref={ref}
        data-slot="input-group-text"
        className={cn(
          "px-3 py-2 text-sm text-muted-foreground",
          className,
        )}
        {...props}
      />
    );
  },
);
InputGroupText.displayName = "InputGroupText";

// ============================================================================
// InputGroupButton
// ============================================================================

type InputGroupButtonVariantProps = VariantProps<typeof buttonVariants>;

interface InputGroupButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    InputGroupButtonVariantProps {
  asChild?: boolean;
}

const InputGroupButton = React.forwardRef<
  HTMLButtonElement,
  InputGroupButtonProps
>(({ className, variant = "ghost", size = "icon-sm", ...props }, ref) => {
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
});
InputGroupButton.displayName = "InputGroupButton";

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
