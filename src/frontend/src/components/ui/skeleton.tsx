import { cn } from "@/lib/utils/cn";

/**
 * Skeleton shimmer placeholder.
 *
 * Uses `bg-muted` so the shimmer respects the design system's
 * muted palette and adapts to light/dark mode automatically.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("bg-muted animate-pulse rounded-md", className)}
      {...props}
    />
  );
}

export { Skeleton };
