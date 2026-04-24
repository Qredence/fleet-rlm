import { cn } from "@/lib/utils";
import type { HTMLAttributes, ReactNode } from "react";

interface NodeBadgeProps extends HTMLAttributes<HTMLSpanElement> {
  children: ReactNode;
}

/**
 * Small badge/pill for graph step nodes and execution metadata.
 *
 * Uses design tokens for consistent sizing and borders.
 * The background color is typically overridden by the parent
 * via Tailwind arbitrary values or CSS custom properties.
 */
export function NodeBadge({ children, className, ...rest }: NodeBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center rounded-full border border-border-subtle/80 bg-muted/25 px-1.5 py-px text-2xs font-medium leading-tight text-foreground/90",
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
