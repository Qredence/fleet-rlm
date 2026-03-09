import { cn } from "@/lib/utils/cn";

interface BrandMarkProps {
  className?: string;
  ariaHidden?: boolean;
}

/**
 * Brand logo mark sourced from frontend public assets.
 * Uses CSS masking so color follows `currentColor`.
 */
export function BrandMark({ className, ariaHidden = true }: BrandMarkProps) {
  return (
    <span
      className={cn("brand-mark-mask inline-block bg-current", className)}
      aria-hidden={ariaHidden}
    />
  );
}
