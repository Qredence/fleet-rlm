import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Shared action button used inside tool cards, approval footers, and question prompts.
 * Replaces the duplicated `h-5 px-1.5 rounded-an-action-sm … bg-an-primary-color …`
 * and `h-6 px-2 rounded-an-action-sm … text-muted-foreground hover:text-an-tool-color`
 * className recipes that were copy-pasted across plan-tool, tool-approval-footer,
 * question-prompt, and input-bar.
 */
const toolActionButtonVariants = cva(
  "inline-flex items-center justify-center font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.98] disabled:opacity-60 disabled:active:scale-100",
  {
    variants: {
      variant: {
        primary:
          "bg-an-primary-color text-an-send-button-color hover:bg-an-primary-color/90 disabled:hover:bg-an-primary-color",
        ghost: "text-muted-foreground hover:text-an-tool-color disabled:opacity-60",
        ghostSoft:
          "text-muted-foreground hover:text-an-tool-color hover:bg-muted/50 disabled:opacity-60 disabled:hover:bg-transparent",
      },
      size: {
        sm: "h-5 px-1.5 rounded-an-action-sm text-xs",
        md: "h-6 px-2 rounded-an-action-sm text-sm",
        mdWide: "h-6 px-2.5 rounded-an-action-sm text-sm",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "sm",
    },
  },
);

export type ToolActionButtonProps = VariantProps<typeof toolActionButtonVariants> & {
  children: ReactNode;
  className?: string;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className">;

export function ToolActionButton({
  variant,
  size,
  className,
  children,
  ...props
}: ToolActionButtonProps) {
  return (
    <button
      type="button"
      className={cn(toolActionButtonVariants({ variant, size }), className)}
      {...props}
    >
      {children}
    </button>
  );
}

export { toolActionButtonVariants };
