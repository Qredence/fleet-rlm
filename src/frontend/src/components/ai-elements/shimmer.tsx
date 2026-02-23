import { cn } from "@/components/ui/utils";

interface ShimmerProps extends React.HTMLAttributes<HTMLDivElement> {
  lines?: number;
}

function Shimmer({ lines = 3, className, ...props }: ShimmerProps) {
  return (
    <div data-slot="shimmer" className={cn("space-y-2", className)} {...props}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className={cn(
            "h-3 rounded bg-muted/70",
            "animate-pulse",
            i === lines - 1 ? "w-2/3" : "w-full",
          )}
        />
      ))}
    </div>
  );
}

export { Shimmer };
export type { ShimmerProps };
