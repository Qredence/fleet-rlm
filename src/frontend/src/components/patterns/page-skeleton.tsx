import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

function PageSkeleton({ isMobile, className }: { isMobile?: boolean; className?: string }) {
  return (
    <div
      className={cn(
        "mx-auto flex w-full max-w-[800px] animate-in fade-in flex-col gap-6 duration-150",
        isMobile ? "p-4" : "p-6",
        className,
      )}
    >
      <div className="flex flex-col gap-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-72" />
      </div>

      <div className="flex items-center gap-3">
        <Skeleton className="h-9 max-w-[280px] flex-1 rounded-lg" />
        <Skeleton className="h-9 w-24 rounded-button" />
        <Skeleton className="h-9 w-24 rounded-button" />
      </div>

      <div className="flex flex-col gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-20 w-full rounded-card" />
        ))}
      </div>
    </div>
  );
}

function PanelSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("animate-in fade-in flex flex-col gap-4 p-5 duration-150", className)}>
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
