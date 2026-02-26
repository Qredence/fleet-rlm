import { cn } from "@/components/ui/utils";

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
      className={cn("inline-block bg-current", className)}
      style={{
        maskImage: "url(/branding/logo-mark.svg)",
        WebkitMaskImage: "url(/branding/logo-mark.svg)",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
        maskSize: "contain",
        WebkitMaskSize: "contain",
      }}
      aria-hidden={ariaHidden}
    />
  );
}
