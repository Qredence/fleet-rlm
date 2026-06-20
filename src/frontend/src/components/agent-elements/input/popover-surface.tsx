import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/**
 * Shared popover surface recipe used by input-bar, model-picker, and mode-selector.
 * Replaces the duplicated `rounded-an-action-lg border-border/80 bg-an-input-background
 * p-1 text-an-foreground shadow-lg` className string.
 *
 * Use `popoverSurfaceClass` when composing into a `PopoverContent` className prop
 * (Base UI portals require their own element); use `<PopoverSurface>` for plain divs.
 */
export const popoverSurfaceClass =
  "rounded-an-action-lg border border-border/80 bg-an-input-background p-1 text-an-foreground shadow-lg";

export type PopoverSurfaceProps = ComponentProps<"div"> & {
  /** Tailwind width class for the popover (e.g. "w-46", "w-64", "w-52"). */
  width?: string;
};

export function PopoverSurface({ width, className, children, ...props }: PopoverSurfaceProps) {
  return (
    <div className={cn(popoverSurfaceClass, width, className)} {...props}>
      {children}
    </div>
  );
}
