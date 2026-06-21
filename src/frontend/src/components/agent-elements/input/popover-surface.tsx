import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/**
 * Shared popover surface recipe used by input-bar, model-picker, and mode-selector.
 * Replaces duplicated popover chrome with the haxdesign-style Agent Elements
 * surface: 10px radius, 4px padding, subtle border, and compact menu shadow.
 *
 * Use `popoverSurfaceClass` when composing into a `PopoverContent` className prop
 * (Base UI portals require their own element); use `<PopoverSurface>` for plain divs.
 */
export const popoverSurfaceClass =
  "an-popover-surface border text-an-foreground outline-hidden";

export type PopoverSurfaceProps = ComponentProps<"div"> & {
  /** Tailwind width class for the popover (e.g. "w-46", "w-64", "w-52"). */
  width?: string;
};

export function PopoverSurface({
  width,
  className,
  children,
  ...props
}: PopoverSurfaceProps) {
  return (
    <div className={cn(popoverSurfaceClass, width, className)} {...props}>
      {children}
    </div>
  );
}
