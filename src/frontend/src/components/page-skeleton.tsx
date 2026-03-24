import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Generic full-page loading skeleton.
 *
 * Used as the `Suspense` fallback while lazy-loaded page components
 * are being fetched. Mimics a typical page structure: header area,
 * toolbar, and content rows.
 *
 * All colours come from `bg-muted` via the Skeleton primitive so it
 * adapts to light/dark mode automatically.
 */
function PageSkeleton({
  isMobile,
  className,
}: {
  isMobile?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-6 w-full max-w-[800px] mx-auto animate-in fade-in duration-150",
        isMobile ? "p-4" : "p-6",
        className,
      )}
    >
      {/* Header area */}
      <div className="flex flex-col gap-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-72" />
      </div>

      {/* Toolbar / filter row */}
      <div className="flex items-center gap-3">
        <Skeleton className="h-9 flex-1 max-w-[280px] rounded-lg" />
        <Skeleton className="h-9 w-24 rounded-button" />
        <Skeleton className="h-9 w-24 rounded-button" />
      </div>

      {/* Content rows */}
      <div className="flex flex-col gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-20 w-full rounded-card" />
        ))}
      </div>
    </div>
  );
}

/**
 * Compact skeleton for builder-panel content areas.
 *
 * Smaller than the full PageSkeleton — just a few lines and a content block.
 */
function PanelSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex flex-col gap-4 p-5 animate-in fade-in duration-150",
        className,
      )}
    >
      <div className="flex flex-col gap-2">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-3.5 w-64" />
      </div>
      <Skeleton className="h-px w-full bg-border" />
      <div className="flex flex-col gap-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
        <Skeleton className="h-4 w-4/6" />
      </div>
      <Skeleton className="h-32 w-full rounded-lg" />
    </div>
  );
}

export { PageSkeleton, PanelSkeleton };
